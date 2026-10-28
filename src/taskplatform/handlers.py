"""Synthetic task handlers.

A handler takes the task payload and returns a result dict, or raises to
signal failure. Handlers are deterministic so tests are repeatable.
"""

import time
from collections.abc import Callable
from typing import Any

from taskplatform.models import TaskType

MAX_SLEEP_MS = 10_000

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def sleep_handler(payload: dict[str, Any]) -> dict[str, Any]:
    duration_ms = payload.get("duration_ms", 100)
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or not 0 <= duration_ms <= MAX_SLEEP_MS
    ):
        raise ValueError(f"duration_ms must be an integer between 0 and {MAX_SLEEP_MS}")
    time.sleep(duration_ms / 1000)
    return {"slept_ms": duration_ms}


def fail_handler(payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError(payload.get("message", "synthetic failure"))


HANDLERS: dict[TaskType, Handler] = {
    TaskType.SLEEP: sleep_handler,
    TaskType.FAIL: fail_handler,
}
