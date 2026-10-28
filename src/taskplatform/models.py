"""Task data model shared by the API and the worker."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class TaskType(StrEnum):
    SLEEP = "sleep"
    FAIL = "fail"
    RESIZE = "resize"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskRequest(BaseModel):
    """Body of POST /tasks."""

    type: TaskType
    payload: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    """Full task record, stored in Redis and returned by the API."""

    id: str
    type: TaskType
    payload: dict[str, Any]
    status: TaskStatus
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
