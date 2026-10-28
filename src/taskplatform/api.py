"""HTTP API for bounded image uploads and background task status."""

from functools import lru_cache
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from redis import Redis
from redis.exceptions import RedisError

from taskplatform.config import get_settings
from taskplatform.image_service import (
    ImageStorage,
    InvalidImageError,
    UploadTooLargeError,
    UnsupportedImageError,
)
from taskplatform.models import Task, TaskRequest, TaskStatus, TaskType, utcnow
from taskplatform.queue import TaskQueue
from taskplatform.store import TaskStore


@lru_cache
def get_redis() -> Redis:
    # Overridden with fakeredis in tests.
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def get_store(redis: Annotated[Redis, Depends(get_redis)]) -> TaskStore:
    return TaskStore(redis)


def get_queue(redis: Annotated[Redis, Depends(get_redis)]) -> TaskQueue:
    settings = get_settings()
    return TaskQueue(redis, settings.queue_name, lease_seconds=settings.lease_seconds)


app = FastAPI(title="k8s-task-platform")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    # Liveness only: does not check Redis.
    return {"status": "ok"}


@app.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
def create_task(
    request: TaskRequest,
    queue: Annotated[TaskQueue, Depends(get_queue)],
) -> Task:
    if request.type == TaskType.RESIZE:
        raise HTTPException(status_code=422, detail="use POST /images for resize tasks")
    now = utcnow()
    task = Task(
        id=str(uuid4()),
        type=request.type,
        payload=request.payload,
        status=TaskStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    # The task record and queue entry become visible in the same Redis script.
    queue.enqueue(task.id, task.model_dump_json())
    return task


@app.post("/images", status_code=status.HTTP_202_ACCEPTED)
async def create_image_task(
    queue: Annotated[TaskQueue, Depends(get_queue)],
    file: UploadFile = File(...),
) -> Task:
    settings = get_settings()
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=422, detail="image file is empty")

    task_id = str(uuid4())
    storage = ImageStorage(settings.storage_dir)
    try:
        uploaded = storage.store_upload(
            data,
            task_id,
            max_bytes=settings.max_upload_bytes,
            max_pixels=settings.max_image_pixels,
            max_dimension=settings.max_image_dimension,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedImageError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    now = utcnow()
    task = Task(
        id=task_id,
        type=TaskType.RESIZE,
        payload={
            "input_key": uploaded.input_key,
            "output_key": uploaded.output_key,
            "max_dimension": settings.output_max_dimension,
            "image_format": uploaded.image_format.lower(),
            "original_width": uploaded.width,
            "original_height": uploaded.height,
        },
        status=TaskStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    try:
        queue.enqueue(task.id, task.model_dump_json())
    except RedisError as exc:
        storage.path_for(uploaded.input_key).unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="task queue is unavailable") from exc
    return task


@app.get("/tasks/{task_id}/result")
def download_task_result(task_id: str, store: Annotated[TaskStore, Depends(get_store)]):
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != TaskStatus.SUCCEEDED or task.result is None:
        raise HTTPException(status_code=409, detail="image result is not ready")

    try:
        path = ImageStorage(get_settings().storage_dir).path_for(task.payload["output_key"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="image result not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image result not found")

    image_format = task.result.get("format")
    media_types = {"jpeg": ("image/jpeg", "jpg"), "png": ("image/png", "png")}
    if image_format not in media_types:
        raise HTTPException(status_code=404, detail="image result not found")
    media_type, extension = media_types[image_format]
    return FileResponse(path, media_type=media_type, filename=f"resized-{task.id}.{extension}")


@app.get("/tasks/{task_id}")
def get_task(task_id: str, store: Annotated[TaskStore, Depends(get_store)]) -> Task:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return task
