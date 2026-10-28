"""Task state storage: one JSON string per task at key ``task:{id}``."""

from redis import Redis

from taskplatform.models import Task


def _key(task_id: str) -> str:
    return f"task:{task_id}"


class TaskStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def save(self, task: Task) -> None:
        # M1 limitation: no TTL, so records accumulate forever.
        self._redis.set(_key(task.id), task.model_dump_json())

    def get(self, task_id: str) -> Task | None:
        raw = self._redis.get(_key(task_id))
        if raw is None:
            return None
        return Task.model_validate_json(raw)
