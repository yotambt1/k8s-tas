# k8s-task-platform

A small image-resize service for learning how to run background work with
FastAPI, Redis and a separate Python worker. Upload a JPEG or PNG, receive a
task ID, poll its status, then download the resized image.

**Current state:** local API/worker service, input limits, shared local
storage, reliable Redis lease/ack flow and recovery tests; a Docker image with
Compose; Minikube manifests and an equivalent Helm chart; and GitHub Actions
CI that runs the tests and verifies the image build. Migrating the running
deployment to Helm, image publishing and GitOps are the next learning
milestones.

## Request flow

```text
client --POST /images--> API --atomic task record + queue ID--> Redis
client <--202 + task ID-- API                                  |
                                                                v
                     Worker <--reserve with lease/token---------+
                     Worker --read shared uploads/ --> resize --> results/
                     Worker --save terminal state + ack atomically--> Redis
client --GET /tasks/{id}--> status / result metadata
client --GET /tasks/{id}/result--> resized image bytes
```

The API never processes the image. Redis stores task records and queue/lease
state; original and resized image files live under `STORAGE_DIR`, which must be
the same shared, writable directory for the API and every worker.

## HTTP API

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Liveness check; does not check Redis |
| POST | `/images` | Multipart field `file`; JPEG/PNG only; returns `202` and task |
| GET | `/tasks/{id}` | Status, attempt count, result metadata or error |
| GET | `/tasks/{id}/result` | Download the completed image; `409` while not ready |

Default input limits are 10 MiB, 40 million decoded pixels, and 10,000 pixels
per side. Output thumbnails fit within 512 × 512 and preserve JPEG or PNG.
Limits are configurable with environment variables. Legacy `/tasks` synthetic
`sleep` and `fail` handlers remain for queue-focused tests; `resize` jobs must
be submitted through `/images` so clients cannot choose filesystem paths.

## Run locally

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), and Redis.

```sh
uv sync
uv run pytest -v
```

Start Redis, then run these in two terminals from the project directory:

```sh
uv run uvicorn taskplatform.api:app --reload
uv run python -m taskplatform.worker
```

Upload and inspect an image:

```sh
TASK_ID=$(curl -fsS -F 'file=@photo.jpg' http://127.0.0.1:8000/images \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "$TASK_ID"
curl "http://127.0.0.1:8000/tasks/$TASK_ID"
curl -OJ "http://127.0.0.1:8000/tasks/$TASK_ID/result"
```

If API and worker run from different directories or containers, set
`STORAGE_DIR` to the same shared volume path in both. Redis defaults to
`redis://localhost:6379/0`, queue name `tasks`, and lease duration 30 seconds.

## Run with Docker Compose

`Dockerfile` builds one application image. Compose starts separate API and
worker containers from that image, plus Redis. Both app containers mount the
same `image-data` volume; Redis persists its AOF data in `redis-data`.

```sh
docker compose up --build
```

The API is available at `http://127.0.0.1:8000`. Stop the containers with
`docker compose down`; named volumes are kept. `docker compose down -v` also
deletes Redis and image data, so use it only when you intend to remove them.

## Local Kubernetes with Minikube

The `k8s/` directory contains PVCs, a Redis Deployment and internal Service,
and separate API and Worker Deployments. The API is exposed only as a
`ClusterIP`; use `kubectl port-forward` for local access. Step-by-step image
loading, rollout checks and an upload/download smoke test are in
[`k8s/README.md`](k8s/README.md).

The image-data PVC is `ReadWriteOnce`: it can be shared by the API and Worker
because Minikube is a single-node cluster. It is local cluster storage, not a
multi-node or production storage solution; use shared RWX storage or object
storage before moving the pods to different nodes. Deleting the Minikube
cluster also deletes its local volumes.

### Helm chart

[`charts/task-platform`](charts/task-platform/) packages the same resources as
a Helm release, with values for images, replicas, resources, Service ports,
environment and storage. Its default render matches the `k8s/` manifests, so it
can adopt the running kubectl deployment in place, without restarting pods;
existing PVCs are reused through `existingClaim` and stay outside Helm
ownership. The adoption is a step-by-step runbook with preflight checks,
backups and rollback in [`docs/helm-migration.md`](docs/helm-migration.md);
nothing in it runs automatically.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to
`main` and on every pull request, with two parallel jobs:

- **Tests:** installs the pinned uv, runs `uv sync --locked` (fails if
  `uv.lock` is stale) and `uv run pytest -v`. Tests use `fakeredis`, so no
  Redis service is needed.
- **Docker image:** builds the image locally on the runner, checks that it runs
  as a non-root user and that the worker module imports, then starts the API
  container and polls `GET /healthz`.

The workflow token is read-only (`contents: read`), uses no secrets, never logs
in to a registry, pushes no image and deploys nothing. Actions are pinned to
full commit SHAs. The uv version is pinned in both the workflow and the
`Dockerfile`; update them together.

Reproduce the CI checks locally:

```sh
uv sync --locked && uv run pytest -v
docker build --tag k8s-task-platform:ci .
```

## Reliability and limits

- Redis Lua scripts atomically create a task record with its queue entry,
  reserve work, fence state writes by the current lease token, and save the
  final state while acknowledging the task.
- If a worker dies before ack, another worker reclaims the task after its lease
  expires. Terminal duplicate entries are acknowledged without reprocessing.
  Delivery is **at least once**, not exactly once.
- Lease renewal, retry backoff/dead-lettering, task-record expiry, graceful
  shutdown, authentication, and a Redis-aware readiness probe are not built.
  Keep `LEASE_SECONDS` longer than normal image-processing time; worker clocks
  should be synchronized.
- The local file store is suitable for this learning project only. A future
  container/Kubernetes deployment must mount one shared persistent volume or
  replace it with object storage.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `QUEUE_NAME` | `tasks` | Pending queue key prefix |
| `LEASE_SECONDS` | `30` | Worker reservation lifetime |
| `STORAGE_DIR` | `./data` | Shared local image storage |
| `MAX_UPLOAD_BYTES` | `10485760` | Maximum upload bytes |
| `MAX_IMAGE_PIXELS` | `40000000` | Maximum decoded pixel count |
| `MAX_IMAGE_DIMENSION` | `10000` | Maximum width or height |
| `OUTPUT_MAX_DIMENSION` | `512` | Maximum thumbnail side length |
