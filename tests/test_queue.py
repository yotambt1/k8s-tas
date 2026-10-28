"""Contract tests for the reliable queue: reserve -> process -> ack.

Time is controlled with FakeClock, so lease expiry is deterministic and no
test sleeps. A worker "crash" is simulated by reserving a task and never
acknowledging it, which is exactly what Redis observes when a worker dies.
"""

from taskplatform.queue import TaskQueue

LEASE = 30


class FakeClock:
    """Injectable clock: returns a fixed time until advanced explicitly."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_unacked_task_is_redelivered_to_another_worker_after_lease_expires(redis):
    clock = FakeClock()
    worker_a = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_b = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_a.enqueue("t1")

    first = worker_a.reserve()
    assert first.task_id == "t1"
    assert first.deadline == clock() + LEASE
    assert first.delivery == 1

    # Worker A "crashes": it never calls ack().
    clock.advance(LEASE - 1)
    assert worker_b.reserve() is None  # lease still valid: no stealing

    clock.advance(1)  # now == deadline: the lease has expired
    second = worker_b.reserve()  # reserve() reclaims expired leases first
    assert second.task_id == "t1"
    assert second.token != first.token
    assert second.deadline == clock() + LEASE
    assert second.delivery == 2


def test_acked_task_is_not_redelivered_after_lease_expires(redis):
    clock = FakeClock()
    worker_a = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_b = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_a.enqueue("t1")
    reservation = worker_a.reserve()

    assert worker_a.ack("t1", reservation.token) is True

    clock.advance(LEASE * 2)
    assert worker_b.reserve() is None
    assert worker_b.size() == 0


def test_duplicate_pending_id_cannot_steal_a_live_lease(redis):
    clock = FakeClock()
    worker_a = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_b = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_a.enqueue("t1")
    first = worker_a.reserve()
    worker_b.enqueue("t1")

    assert worker_b.reserve() is None
    assert redis.hget("tasks:tokens", "t1") == first.token
    assert redis.hget("tasks:deliveries", "t1") == "1"

    clock.advance(LEASE)
    retry = worker_b.reserve()
    assert retry.task_id == "t1"
    assert retry.token != first.token
    assert retry.delivery == 2


def test_stale_ack_is_rejected_and_leaves_new_lease_intact(redis):
    clock = FakeClock()
    worker_a = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_b = TaskQueue(redis, "tasks", lease_seconds=LEASE, clock=clock)
    worker_a.enqueue("t1")
    stale = worker_a.reserve()
    clock.advance(LEASE)
    current = worker_b.reserve()
    assert current.token != stale.token

    # Lease state is checked in Redis directly: through the public API a
    # deleted lease and an intact one both look like "reserve() is None".
    def lease_state():
        return (
            redis.zscore("tasks:leases", "t1"),
            redis.hget("tasks:tokens", "t1"),
            redis.hget("tasks:deliveries", "t1"),
        )

    before = lease_state()
    assert worker_a.ack("t1", stale.token) is False
    assert lease_state() == before

    assert worker_b.ack("t1", current.token) is True
