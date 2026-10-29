# Run the image service on Minikube

These manifests target a local, single-node Minikube cluster. They are kept
separate so the storage claims, Redis, and application workloads can be read
one piece at a time.

## 1. Start the local cluster

If the `task-platform` profile does not exist yet, create it:

```sh
minikube start -p task-platform --driver=docker --cpus=2 --memory=2048
```

Before applying anything, check that `kubectl` is pointed at this profile:

```sh
kubectl config current-context
```

It should print `task-platform`. `minikube start` selects that context; if a
different context is active, stop and switch to the intended Minikube context
before applying the manifests.

## 2. Build and load the app image

Minikube has its own image store. Build the app image on the Mac, then copy it
to the `task-platform` node. Redis uses the official `redis:8-alpine` image and
is pulled by Kubernetes if it is not already present.

```sh
docker compose build api
minikube image load -p task-platform k8s-task-platform:local
```

The API and Worker use this same `k8s-task-platform:local` image; only their
container commands differ.

## 3. Create the Kubernetes resources

Apply the files in order: claims first, then Redis and its internal Service,
then the API, its Service, and the Worker.

```sh
kubectl apply -f k8s/storage.yaml
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/app.yaml
```

The app pods have a small init container that waits for Redis to answer `PING`.
Check pod, claim and Service status, then wait for each Deployment rollout:

```sh
kubectl get pods,pvc,services
kubectl rollout status deployment/redis
kubectl rollout status deployment/task-api
kubectl rollout status deployment/task-worker
```

## 4. Upload and download an image

In one terminal, forward the internal API Service to localhost. Leave this
command running while testing from a second terminal:

```sh
kubectl port-forward service/task-api 8000:8000
```

In the second terminal, replace the example path with a real local JPEG or
PNG. The upload returns a task ID. Repeat the status request until it reports
`"status":"succeeded"`, then download the result:

```sh
IMAGE_PATH="/absolute/path/to/your-image.png"
TASK_JSON=$(curl -fsS -F "file=@${IMAGE_PATH}" http://127.0.0.1:8000/images)
printf '%s\n' "$TASK_JSON"
TASK_ID=$(printf '%s\n' "$TASK_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS "http://127.0.0.1:8000/tasks/$TASK_ID"
curl -f -OJ "http://127.0.0.1:8000/tasks/$TASK_ID/result"
```

## Storage scope

Both app pods mount `image-data` at `/data`. The claim uses `ReadWriteOnce`,
which permits these two pods to share it while they run on the same Minikube
node. Redis separately mounts `redis-data` and writes its AOF there. Minikube's
default StorageClass backs both claims with local node storage.

This is persistent across pod restarts, but it is not shared multi-node storage
or a backup. Deleting the Minikube cluster deletes its local volumes. For a
multi-node cluster, use a storage class that supports shared `ReadWriteMany`
access or move image files to object storage; keep one replica for this
single-node learning setup.
