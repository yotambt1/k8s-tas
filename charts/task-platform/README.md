# task-platform Helm chart

Packages the image-resize service from [`k8s/`](../../k8s/) as one Helm
release: a Redis Deployment and Service, the API Deployment and Service, and
the Worker Deployment, plus optional PersistentVolumeClaims.

With default values the chart renders the same Deployments and Services as the
kubectl manifests: the same names, selectors, pod templates, replica counts,
images, probes, security context and update strategy. Only top-level object
labels are added. This is what lets the chart adopt the existing deployment
without restarting pods; see [`docs/helm-migration.md`](../../docs/helm-migration.md).

## Fixed resource names

Resource names are fixed (`redis`, `task-api`, `task-worker`, `image-data`,
`redis-data`) instead of being prefixed with the release name: adoption needs
the existing names, and the app's `REDIS_URL` uses the Service name `redis`.
Install at most one release per namespace.

## Values

| Key | Default | Notes |
|---|---|---|
| `image.repository` / `image.tag` / `image.pullPolicy` | `k8s-task-platform` / `local` / `IfNotPresent` | Shared by the API and the worker |
| `podSecurityContext` | `{fsGroup: 10001}` | API and worker pods; makes `/data` writable for the app user |
| `config.queueName` | `tasks` | `QUEUE_NAME` |
| `config.leaseSeconds` | `"30"` | `LEASE_SECONDS`; keep it a string |
| `config.storageDir` | `/data` | `STORAGE_DIR`; also the mount path of `image-data` |
| `config.extraEnv` | `[]` | Extra env entries for both the API and the worker |
| `api.replicas` | `1` | More than 1 requires all pods on one node (RWO storage) |
| `api.containerPort` | `8000` | Also passed to `uvicorn --port` |
| `api.service.type` / `api.service.port` | `ClusterIP` / `8000` | No ingress; use `kubectl port-forward` |
| `api.resources` | `{}` | No requests or limits by default |
| `worker.replicas` | `1` | Same RWO caveat as the API |
| `worker.resources` | `{}` | |
| `redis.image.*` | `redis` / `8-alpine` / `IfNotPresent` | Also used by the `wait-for-redis` init container |
| `redis.service.port` | `6379` | Feeds `REDIS_URL` and the init container check |
| `redis.resources` | `{}` | Redis always runs exactly one replica |
| `persistence.imageData.existingClaim` | `""` | Set to mount an existing claim the chart does not manage |
| `persistence.imageData.size` / `accessModes` / `storageClassName` | `1Gi` / `[ReadWriteOnce]` / `""` | Used only when the chart creates the claim |
| `persistence.redisData.*` | same as `imageData` | |

Any change to images, env, resources, replicas' pod spec or probes changes a
pod template and causes a rollout on `helm upgrade`.

## Storage

- **No `existingClaim`** (fresh cluster): the chart creates `image-data` and
  `redis-data` with `helm.sh/resource-policy: keep`, so `helm uninstall`
  leaves the claims and their data in place.
- **With `existingClaim`** ([`values-existing-pvcs.yaml`](values-existing-pvcs.yaml)):
  the chart only mounts the claims. Helm never creates, patches or deletes
  them. Use this when the claims already exist, such as when adopting the
  kubectl deployment.

If the claims already exist and you install *without* the overlay, Helm
refuses to create them (ownership conflict) and the install fails before
changing anything.

Redis keeps the Deployment default update strategy (`RollingUpdate`), like the
kubectl manifest. A Redis pod template change would briefly run two Redis pods
on the same AOF volume. Switching Redis to `Recreate` is a planned, separate
change.

## Install on a fresh cluster

Build and load the image first (see [`k8s/README.md`](../../k8s/README.md)
step 2), then:

```sh
helm install task-platform charts/task-platform --namespace <namespace>
```

To adopt the existing kubectl-managed deployment instead, follow
[`docs/helm-migration.md`](../../docs/helm-migration.md). Do not run
`helm install` against those resources without completing its preflight
checks.

## Uninstall

`helm uninstall` deletes the release's Deployments and Services. Claims created
by the chart are kept; external claims are never touched. For an adopted
deployment, use the rollback steps in the migration runbook instead.
