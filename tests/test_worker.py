import pytest

from taskplatform import handlers
from taskplatform.models import Task, TaskStatus, TaskType, utcnow
from taskplatform.queue import TaskQueue
from taskplatform.worker import process_one


def make_task(store, queue, task_type=TaskType.SLEEP, payload=None, task_id="t1"):
    now = utcnow()
    task = Task(
        id=task_id,
        type=task_type,
        payload=payload if payload is not None else {"duration_ms": 0},
        status=TaskStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    store.save(task)
    queue.enqueue(task.id)
    return task


def test_sleep_task_succeeds(store, queue):
    make_task(store, queue, payload={"duration_ms": 5})

    assert process_one(queue, store) is True

    task = store.get("t1")
    assert task.status == TaskStatus.SUCCEEDED
    assert task.result == {"slept_ms": 5}
    assert task.attempts == 1
    assert task.error is None
    assert queue.size() == 0


def test_fail_task_is_marked_failed(store, queue):
    make_task(store, queue, task_type=TaskType.FAIL, payload={"message": "boom"})

    process_one(queue, store)

    task = store.get("t1")
    assert task.status == TaskStatus.FAILED
    assert task.error == "RuntimeError: boom"
    assert task.result is None


def test_invalid_payload_is_marked_failed(store, queue):
    make_task(store, queue, payload={"duration_ms": "soon"})

    process_one(queue, store)

    task = store.get("t1")
    assert task.status == TaskStatus.FAILED
    assert task.error.startswith("ValueError")


def test_queued_id_without_record_is_skipped(store, queue):
    queue.enqueue("ghost")

    assert process_one(queue, store) is True
    assert store.get("ghost") is None


class WorkerCrash(BaseException):
    """Simulates abrupt process death (not a normal handler exception)."""


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_crashed_worker_task_is_retried_after_lease_expiry(redis, store, monkeypatch):
    clock = FakeClock()
    worker_a = TaskQueue(redis, "tasks", lease_seconds=10, clock=clock)
    worker_b = TaskQueue(redis, "tasks", lease_seconds=10, clock=clock)
    make_task(store, worker_a)

    def crash(payload):
        raise WorkerCrash()

    monkeypatch.setitem(handlers.HANDLERS, TaskType.SLEEP, crash)
    with pytest.raises(WorkerCrash):
        process_one(worker_a, store)
    assert store.get("t1").status == TaskStatus.RUNNING
    assert redis.zscore("tasks:leases", "t1") == 1_010.0

    clock.advance(10)
    monkeypatch.setitem(handlers.HANDLERS, TaskType.SLEEP, lambda payload: {"slept_ms": 0})
    assert process_one(worker_b, store) is True
    task = store.get("t1")
    assert task.status == TaskStatus.SUCCEEDED
    assert task.attempts == 2
    assert redis.zscore("tasks:leases", "t1") is None


def test_duplicate_queue_entry_does_not_rerun_terminal_task(store, queue):
    make_task(store, queue)
    queue.enqueue("t1")

    process_one(queue, store)
    process_one(queue, store)

    task = store.get("t1")
    assert task.status == TaskStatus.SUCCEEDED
    assert task.attempts == 1
