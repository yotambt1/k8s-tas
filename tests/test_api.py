def test_healthz(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_task_returns_202_and_enqueues(client, queue):
    response = client.post("/tasks", json={"type": "sleep", "payload": {"duration_ms": 0}})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["attempts"] == 0
    assert body["id"]
    assert queue.size() == 1


def test_get_task_returns_stored_task(client):
    created = client.post("/tasks", json={"type": "sleep"}).json()

    response = client.get(f"/tasks/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_task_returns_404(client):
    response = client.get("/tasks/does-not-exist")

    assert response.status_code == 404


def test_unknown_task_type_is_rejected(client, queue):
    response = client.post("/tasks", json={"type": "mine-bitcoin"})

    assert response.status_code == 422
    assert queue.size() == 0
