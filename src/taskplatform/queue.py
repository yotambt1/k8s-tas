"""FIFO Redis queue with expiring worker leases and token-fenced ack.

A reserved task stays in the lease sorted set until its current holder saves
completion and acknowledges it. Expired, unacknowledged tasks are redelivered.
Lease state is shared in Redis; the injectable clock makes recovery tests
repeatable. Production workers should use synchronized clocks and a lease long
enough for the image operation (lease renewal is a later reliability step).
"""
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from redis import Redis

# Runs atomically inside Redis: pending work or an expired lease is claimed
# by exactly one worker. KEYS: pending, leases, tokens, deliveries.
# ARGV: now, deadline, token.
_RESERVE_SCRIPT = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1], 'LIMIT', 0, 1)
local task_id
if #expired > 0 then
    task_id = expired[1]
else
    -- Skip duplicate pending IDs while that task already has a live lease.
    local pending = redis.call('LLEN', KEYS[1])
    for i = 1, pending do
        local candidate = redis.call('RPOP', KEYS[1])
        if not candidate then break end
        if not redis.call('ZSCORE', KEYS[2], candidate) then
            task_id = candidate
            break
        end
        redis.call('LPUSH', KEYS[1], candidate)
    end
    if not task_id then return false end
end
redis.call('ZADD', KEYS[2], ARGV[2], task_id)
redis.call('HSET', KEYS[3], task_id, ARGV[3])
local delivery = redis.call('HINCRBY', KEYS[4], task_id, 1)
return {task_id, delivery}
"""

_ENQUEUE_SCRIPT = """
if ARGV[2] ~= '' then redis.call('SET', KEYS[2], ARGV[2]) end
redis.call('LPUSH', KEYS[1], ARGV[1])
return 1
"""

_SAVE_IF_CURRENT_SCRIPT = """
if redis.call('HGET', KEYS[2], ARGV[1]) ~= ARGV[2] then return 0 end
local deadline = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not deadline or tonumber(deadline) <= tonumber(ARGV[3]) then return 0 end
redis.call('SET', KEYS[3], ARGV[4])
return 1
"""

_ACK_SCRIPT = """
if redis.call('HGET', KEYS[2], ARGV[1]) ~= ARGV[2] then return 0 end
local deadline = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not deadline or tonumber(deadline) <= tonumber(ARGV[3]) then return 0 end
if ARGV[4] ~= '' then redis.call('SET', KEYS[4], ARGV[4]) end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('HDEL', KEYS[3], ARGV[1])
return 1
"""

@dataclass(frozen=True)
class Reservation:
    task_id: str
    token: str
    deadline: float
    delivery: int


class TaskQueue:
    def __init__(
        self,
        redis: Redis,
        name: str,
        lease_seconds: float = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._name = name
        self._leases_key = f"{name}:leases"
        self._tokens_key = f"{name}:tokens"
        self._deliveries_key = f"{name}:deliveries"
        self._lease_seconds = lease_seconds
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._lease_seconds = lease_seconds
        self._clock = clock
        self._reserve_script = redis.register_script(_RESERVE_SCRIPT)
        self._enqueue_script = redis.register_script(_ENQUEUE_SCRIPT)
        self._save_if_current_script = redis.register_script(_SAVE_IF_CURRENT_SCRIPT)
        self._ack_script = redis.register_script(_ACK_SCRIPT)

    def enqueue(self, task_id: str, task_json: str | None = None) -> None:
        """Enqueue an ID, optionally writing its task record atomically too."""
        self._enqueue_script(
            keys=[self._name, f"task:{task_id}"],
            args=[task_id, task_json or ""],
        )

    def reserve(self) -> Reservation | None:
        """Lease an expired task first, otherwise the oldest pending task."""
        now = self._clock()
        deadline = now + self._lease_seconds
        token = uuid.uuid4().hex
        result = self._reserve_script(
            keys=[self._name, self._leases_key, self._tokens_key, self._deliveries_key],
            args=[now, deadline, token],
        )
        if result is None or result is False:
            return None
        task_id, delivery = result
        return Reservation(task_id=task_id, token=token, deadline=deadline, delivery=int(delivery))

    def save_if_current(self, task_id: str, token: str, task_json: str) -> bool:
        """Persist an in-progress state only while this token owns the lease."""
        result = self._save_if_current_script(
            keys=[self._leases_key, self._tokens_key, f"task:{task_id}"],
            args=[task_id, token, self._clock(), task_json],
        )
        return bool(result)

    def ack(self, task_id: str, token: str, task_json: str | None = None) -> bool:
        """Acknowledge only the live lease; optionally save completion atomically."""
        result = self._ack_script(
            keys=[self._leases_key, self._tokens_key, self._deliveries_key, f"task:{task_id}"],
            args=[task_id, token, self._clock(), task_json or ""],
        )
        return bool(result)

    def size(self) -> int:
        """Number of IDs not currently leased."""
        return self._redis.llen(self._name)
