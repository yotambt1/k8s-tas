"""Worker: pulls task IDs from the queue and runs their handlers.

Run with: ``python -m taskplatform.worker`` (needs a real Redis; from M3).
"""

import logging
import time
from pathlib import Path

from redis import Redis

from taskplatform.config import get_settings
from taskplatform.handlers import HANDLERS
from taskplatform.image_service import resize_image
from taskplatform.models import TaskStatus, TaskType, utcnow
from taskplatform.queue import TaskQueue
from taskplatform.store import TaskStore

logger = logging.getLogger(__name__)


def process_one(
    queue: TaskQueue,
    store: TaskStore,
    timeout: float = 1,
    storage_dir: str | Path | None = None,
) -> bool:
    """Process at most one leased task; expired work is safely redelivered."""
    reservation = queue.reserve()
    if reservation is None:
        if timeout > 0:
            time.sleep(timeout)
        return False

    task = store.get(reservation.task_id)
    if task is None:
        logger.warning("task %s was leased but has no record; acknowledging it", reservation.task_id)
        queue.ack(reservation.task_id, reservation.token)
        return True

    # A duplicate queue entry must not re-run an already terminal task.
    if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
        queue.ack(task.id, reservation.token)
        return True

    task = task.model_copy(
        update={
            "status": TaskStatus.RUNNING,
            "attempts": task.attempts + 1,
            "updated_at": utcnow(),
        }
    )
    if not queue.save_if_current(task.id, reservation.token, task.model_dump_json()):
        logger.warning("lease for task %s expired before processing began", task.id)
        return True

    try:
        if task.type == TaskType.RESIZE:
            result = resize_image(task.payload, storage_dir or get_settings().storage_dir)
        else:
            result = HANDLERS[task.type](task.payload)
    except Exception as exc:
        logger.info("task %s failed: %s", task.id, exc)
        task = task.model_copy(
            update={
                "status": TaskStatus.FAILED,
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": utcnow(),
            }
        )
    else:
        logger.info("task %s succeeded", task.id)
        task = task.model_copy(
            update={
                "status": TaskStatus.SUCCEEDED,
                "result": result,
                "error": None,
                "updated_at": utcnow(),
            }
        )

    # Completion is saved and the lease removed in one Redis script. If this
    # worker's lease expired, a newer worker owns the task and wins the fence.
    if not queue.ack(task.id, reservation.token, task.model_dump_json()):
        logger.warning("could not acknowledge task %s: lease is no longer current", task.id)
    return True


def run_forever(queue: TaskQueue, store: TaskStore) -> None:
    while True:
        process_one(queue, store)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    logger.info("worker started, queue=%s", settings.queue_name)
    run_forever(
        TaskQueue(redis, settings.queue_name, lease_seconds=settings.lease_seconds),
        TaskStore(redis),
    )


if __name__ == "__main__":
    main()
