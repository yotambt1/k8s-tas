"""End-to-end: API -> queue -> worker -> API, all on one fake Redis."""

from taskplatform.worker import process_one


def test_task_flows_from_api_through_worker(client, queue, store):
    created = client.post("/tasks", json={"type": "sleep", "payload": {"duration_ms": 10}}).json()
    assert client.get(f"/tasks/{created['id']}").json()["status"] == "queued"

    assert process_one(queue, store) is True

    body = client.get(f"/tasks/{created['id']}").json()
    assert body["status"] == "succeeded"
    assert body["result"] == {"slept_ms": 10}
    assert body["attempts"] == 1


def test_failed_task_reports_error_through_api(client, queue, store):
    created = client.post("/tasks", json={"type": "fail"}).json()

    process_one(queue, store)

    body = client.get(f"/tasks/{created['id']}").json()
    assert body["status"] == "failed"
    assert body["error"] == "RuntimeError: synthetic failure"


def test_tasks_are_processed_in_fifo_order(client, queue, store):
    first = client.post("/tasks", json={"type": "sleep", "payload": {"duration_ms": 0}}).json()
    second = client.post("/tasks", json={"type": "sleep", "payload": {"duration_ms": 0}}).json()

    process_one(queue, store)

    assert client.get(f"/tasks/{first['id']}").json()["status"] == "succeeded"
    assert client.get(f"/tasks/{second['id']}").json()["status"] == "queued"


def test_prototype_duplicate_submission_creates_two_tasks(client, queue, store):
    # M1 limitation: no Idempotency-Key, so a client retry (e.g. after a
    # network timeout) creates and runs a second, independent task.
    request = {"type": "sleep", "payload": {"duration_ms": 0}}
    a = client.post("/tasks", json=request).json()
    b = client.post("/tasks", json=request).json()

    process_one(queue, store)
    process_one(queue, store)

    assert a["id"] != b["id"]
    assert client.get(f"/tasks/{a['id']}").json()["status"] == "succeeded"
    assert client.get(f"/tasks/{b['id']}").json()["status"] == "succeeded"
