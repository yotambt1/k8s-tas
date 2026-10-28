import fakeredis
import pytest
from fastapi.testclient import TestClient

from taskplatform.api import app, get_redis
from taskplatform.config import get_settings
from taskplatform.queue import TaskQueue
from taskplatform.store import TaskStore


@pytest.fixture
def redis():
    # A dedicated FakeServer per test keeps tests isolated from each other.
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture
def store(redis):
    return TaskStore(redis)


@pytest.fixture
def queue(redis):
    return TaskQueue(redis, get_settings().queue_name)


@pytest.fixture
def client(redis):
    app.dependency_overrides[get_redis] = lambda: redis
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
