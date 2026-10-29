# Migrating the kubectl deployment to Helm

This runbook moves the resources created from [`k8s/`](../k8s/) under a Helm
release **in place**: same names, same pods, same PersistentVolumeClaims. No
pod should restart and no data should move.

**Every step is a separate decision.** Each one is marked:

- **Read-only**: does not change the cluster.
- **Changes the cluster**: run it only after explicit approval, and only after
  the previous steps succeeded.

Stop at the first unexpected result. Do not improvise around a failed check.

## What changes and what does not

| Resource | Owner after migration |
|---|---|
| Deployments `redis`, `task-api`, `task-worker` | Helm release `task-platform` (adopted) |
| Services `redis`, `task-api` | Helm release `task-platform` (adopted) |
| PVCs `image-data`, `redis-data` | **Not Helm.** Mounted via `existingClaim`; unchanged |
| PVs backing the claims | Not Helm; optionally switched to `Retain` (step 3) |

Never run these during the migration: `helm uninstall`, `kubectl delete pvc`,
`kubectl delete -f k8s/`, `helm install --force`, or any `--force-conflicts`
flag.

## Variables

Set these in every shell you use for the migration. `NS` comes from preflight
check P3; do not guess it.

```sh
export RELEASE=task-platform
export CHART=charts/task-platform
export OVERLAY=charts/task-platform/values-existing-pvcs.yaml
export NS=<namespace from P3>
mkdir -p backup
```

`backup/` holds baselines and data copies. Keep it outside version control.

---

## 0. Preflight checks — Read-only

**P1. Tool versions**

```sh
helm version
kubectl version --client
```

- Helm must be **3.13 or newer** for `--dry-run=server`. Upgrade first if it
  is older.
- **Helm 4** applies resources server-side. Rendered values match the live
  objects, so field-manager conflicts are not expected; if step 2 or step 6
  reports a conflict, stop and investigate. Do not force it.

**P2. Cluster context**

```sh
kubectl config current-context
```

Expected: `task-platform` (the Minikube profile). Anything else: stop.

**P3. Namespace of the live resources**

```sh
kubectl get deploy,svc -A -l 'app in (redis,task-api,task-worker)'
kubectl get pvc -A | grep -E 'image-data|redis-data'
```

All three Deployments, both Services and both PVCs must be in **one**
namespace. Export it as `NS`. If they are split across namespaces, stop.

**P4. No existing release**

```sh
helm list -n "$NS" -a
kubectl get secret -n "$NS" -l owner=helm,name="$RELEASE"
```

Both should list nothing for `task-platform`.

**P5. Baseline**

```sh
kubectl get pods -n "$NS" -o wide | tee backup/pods-before.txt
kubectl get deploy,svc,pvc -n "$NS" -o yaml > backup/live-before.yaml
```

Pod names and ages in `pods-before.txt` are the reference for "nothing
restarted" in step 7.

**P6. Volumes and reclaim policy**

```sh
kubectl get pv -o custom-columns='PV:.metadata.name,CLAIM:.spec.claimRef.name,NS:.spec.claimRef.namespace,POLICY:.spec.persistentVolumeReclaimPolicy,STATUS:.status.phase'
```

Note the PV names bound to `image-data` and `redis-data`, and their policy.
Minikube usually creates them with `Delete`: deleting the claim would delete
the data. Step 3 changes that.

---

## 1. Local render — Read-only, no cluster access

```sh
helm lint "$CHART" --strict -f "$OVERLAY"
helm template "$RELEASE" "$CHART" --namespace "$NS" -f "$OVERLAY" > backup/rendered.yaml
grep -E '^kind:' backup/rendered.yaml | sort | uniq -c
```

Expected kinds: 3 `Deployment`, 2 `Service`, **no** `PersistentVolumeClaim`.

## 2. Live-state comparison — Read-only

**2a. Diff the rendered chart against the live objects**

```sh
kubectl diff -n "$NS" -f backup/rendered.yaml
```

`kubectl diff` exits 1 when differences exist; that alone is not an error.

- **Expected:** changes under `metadata.labels` (added `app.kubernetes.io/*`
  and `helm.sh/chart`) and possibly the
  `kubectl.kubernetes.io/last-applied-configuration` annotation.
- **Stop if** anything changes under `spec.selector`, `spec.template`,
  `spec.replicas`, `spec.strategy` or a Service `spec`. A template change
  means the install would restart pods; a selector change would fail as an
  immutable field.

**2b. Confirm Helm detects the ownership conflict**

```sh
helm install "$RELEASE" "$CHART" -n "$NS" -f "$OVERLAY" --dry-run=server
```

- **Expected:** it **fails** with `invalid ownership metadata` for the existing
  Deployments and Services. That proves Helm would not silently take them
  over, and that the conflict is exactly the five objects step 5 adopts.
- It must not mention any PVC. If it does, the overlay was not applied: stop.

## 3. Protect the volumes with `Retain` — Changes the cluster (approval required)

With `Retain`, deleting a claim leaves the PV and its data on the node. This
does not affect running pods.

```sh
kubectl patch pv <image-data PV> -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
kubectl patch pv <redis-data PV> -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
```

Verify with the P6 command: both show `Retain` and stay `Bound`.

To revert later (only after the migration is confirmed and you accept the
risk), patch the policy back to `Delete`.

## 4. Backup and restore verification

**4a. Sentinel task — Changes data (approval required)**

Upload one image through the current deployment so you can prove afterwards
that the data survived.

```sh
kubectl -n "$NS" port-forward service/task-api 8000:8000   # keep running
```

In a second terminal:

```sh
SENTINEL_JSON=$(curl -fsS -F "file=@/absolute/path/to/test.png" http://127.0.0.1:8000/images)
export SENTINEL=$(printf '%s' "$SENTINEL_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "$SENTINEL" | tee backup/sentinel-id.txt
curl -fsS "http://127.0.0.1:8000/tasks/$SENTINEL"          # repeat until "succeeded"
curl -fsS -o backup/sentinel-before.bin "http://127.0.0.1:8000/tasks/$SENTINEL/result"
```

**4b. Back up data — Read-only for workloads (approval required)**

`BGSAVE` makes Redis write a fresh `dump.rdb` next to its AOF; it does not
change any keys.

```sh
kubectl -n "$NS" exec deploy/redis -- redis-cli BGSAVE
kubectl -n "$NS" exec deploy/redis -- redis-cli INFO persistence | grep -E 'rdb_bgsave_in_progress|rdb_last_bgsave_status'
```

Wait until `rdb_bgsave_in_progress:0` and `rdb_last_bgsave_status:ok`, then:

```sh
kubectl -n "$NS" exec deploy/redis -- redis-cli DBSIZE | tee backup/redis-dbsize.txt
kubectl -n "$NS" exec deploy/redis -- redis-cli EXISTS "task:$SENTINEL"   # expect 1

REDIS_POD=$(kubectl -n "$NS" get pod -l app=redis -o jsonpath='{.items[0].metadata.name}')
API_POD=$(kubectl -n "$NS" get pod -l app=task-api -o jsonpath='{.items[0].metadata.name}')
kubectl cp "$NS/$REDIS_POD:/data" backup/redis-data
kubectl cp "$NS/$API_POD:/data" backup/image-data
```

**4c. Verify the backup — Read-only**

```sh
# Image files: checksums in the pod must match the local copy.
kubectl -n "$NS" exec deploy/task-api -- sh -c 'cd /data && find . -type f | sort | xargs -r sha256sum' > backup/image-data.pod.sha256
(cd backup/image-data && find . -type f | sort | xargs shasum -a 256) > backup/image-data.local.sha256
diff backup/image-data.pod.sha256 backup/image-data.local.sha256 && echo "image-data backup OK"

# Redis: the snapshot and the AOF directory are present and non-empty.
ls -la backup/redis-data backup/redis-data/appendonlydir
```

- `diff` must print nothing and then `image-data backup OK`. Files written
  while you copied can cause a mismatch; if so, repeat 4b.
- `backup/redis-data` must contain `dump.rdb` and an `appendonlydir/` with a
  `.manifest` file.

**4d. Restore procedure — Changes the cluster (only if data is lost; separate approval)**

Not part of a normal migration. Use it only if step 7 shows missing data.

1. Scale everything that mounts the claims to zero:
   `kubectl -n "$NS" scale deploy/task-api deploy/task-worker deploy/redis --replicas=0`
2. Start a temporary pod that mounts both claims:

   ```sh
   kubectl -n "$NS" apply -f - <<'EOF'
   apiVersion: v1
   kind: Pod
   metadata:
     name: restore-helper
   spec:
     containers:
       - name: shell
         image: busybox:1.37
         command: ["sleep", "3600"]
         volumeMounts:
           - { name: image-data, mountPath: /restore/image-data }
           - { name: redis-data, mountPath: /restore/redis-data }
     volumes:
       - name: image-data
         persistentVolumeClaim: { claimName: image-data }
       - name: redis-data
         persistentVolumeClaim: { claimName: redis-data }
   EOF
   kubectl -n "$NS" wait --for=condition=Ready pod/restore-helper
   ```

3. Copy the backup in:
   `kubectl cp backup/image-data/. "$NS/restore-helper:/restore/image-data"` and
   `kubectl cp backup/redis-data/. "$NS/restore-helper:/restore/redis-data"`,
   then fix ownership for the app user:
   `kubectl -n "$NS" exec restore-helper -- chown -R 10001:10001 /restore/image-data`
   (only needed if files show a different owner than in the original pod).
4. `kubectl -n "$NS" delete pod restore-helper`, then scale back to 1 with
   `helm upgrade` (if the release exists) or `kubectl scale` (if not).
5. Re-run the checks in step 7.

## 5. Adopt ownership — Changes the cluster (approval required)

Adds Helm's ownership metadata to the five objects. This is metadata only: no
spec changes and no pod restarts. PVCs are deliberately not included.

```sh
kubectl -n "$NS" label deploy/redis deploy/task-api deploy/task-worker svc/redis svc/task-api \
  app.kubernetes.io/managed-by=Helm
kubectl -n "$NS" annotate deploy/redis deploy/task-api deploy/task-worker svc/redis svc/task-api \
  meta.helm.sh/release-name="$RELEASE" meta.helm.sh/release-namespace="$NS"
```

Verify:

```sh
kubectl get pods -n "$NS"                                                  # same names/ages as P5
helm install "$RELEASE" "$CHART" -n "$NS" -f "$OVERLAY" --dry-run=server  # now succeeds
```

If the dry run still fails, undo this step (rollback R1) and stop.

## 6. Install the release — Changes the cluster (approval required)

Run step 2a once more immediately before this; the diff must still contain
only metadata changes.

```sh
helm install "$RELEASE" "$CHART" -n "$NS" -f "$OVERLAY"
```

Helm 4 users: if this reports field-manager conflicts, stop and go to
rollback R1 or R2. Do not add `--force-conflicts`.

## 7. Verify — Read-only (plus one upload)

```sh
helm list -n "$NS"                                           # STATUS: deployed
kubectl get pods -n "$NS" -o wide | tee backup/pods-after.txt
diff <(awk '{print $1}' backup/pods-before.txt) <(awk '{print $1}' backup/pods-after.txt) && echo "no pods replaced"
kubectl -n "$NS" rollout status deployment/redis
kubectl -n "$NS" rollout status deployment/task-api
kubectl -n "$NS" rollout status deployment/task-worker
kubectl get pvc -n "$NS"                                     # both Bound, unchanged
kubectl get pvc -n "$NS" image-data redis-data -o jsonpath='{range .items[*]}{.metadata.name}{" managed-by="}{.metadata.labels.app\.kubernetes\.io/managed-by}{"\n"}{end}'
```

- Pod names must be identical to P5 (`no pods replaced`).
- PVCs must show an empty `managed-by`: they are still outside Helm.

Data survived the migration (with the port-forward from 4a running again):

```sh
curl -fsS "http://127.0.0.1:8000/tasks/$SENTINEL"                          # still "succeeded"
curl -fsS -o backup/sentinel-after.bin "http://127.0.0.1:8000/tasks/$SENTINEL/result"
cmp backup/sentinel-before.bin backup/sentinel-after.bin && echo "sentinel result unchanged"
kubectl -n "$NS" exec deploy/redis -- redis-cli DBSIZE                     # >= backup/redis-dbsize.txt
```

Then run one new upload/download as in [`k8s/README.md`](../k8s/README.md)
step 4.

From now on, change the deployment only with `helm upgrade`. Do **not** run
`kubectl apply -f k8s/` again: it would fight Helm over the same objects.

## 8. Rollback — Changes the cluster (approval required)

Pick the path that matches how far you got. None of them deletes workloads or
claims.

**R1. Before a release exists (step 5 done, step 6 failed or not run):**
remove the adoption metadata.

```sh
kubectl -n "$NS" label deploy/redis deploy/task-api deploy/task-worker svc/redis svc/task-api \
  app.kubernetes.io/managed-by-
kubectl -n "$NS" annotate deploy/redis deploy/task-api deploy/task-worker svc/redis svc/task-api \
  meta.helm.sh/release-name- meta.helm.sh/release-namespace-
```

**R2. After a release exists, to hand the objects back to kubectl:** delete
only Helm's release records, then do R1. The Deployments, Services and pods
are not touched.

```sh
kubectl get secret -n "$NS" -l owner=helm,name="$RELEASE"      # review first
kubectl delete secret -n "$NS" -l owner=helm,name="$RELEASE"
```

The chart's extra `app.kubernetes.io/*` and `helm.sh/chart` labels can stay;
they are not selectors. After R1 and R2, `kubectl apply -f k8s/` manages the
objects again.

**R3. Data lost or corrupted:** follow the restore procedure in 4d.

**Do not** use `helm uninstall` as a rollback for an adopted release: it
deletes the Deployments and Services and causes an outage. (The PVCs would
survive, because they are not part of the release.)

## After the migration (separate changes)

- Switch Redis to `strategy: Recreate`, verified with `kubectl diff` first.
- Retire the `k8s/` manifests once the Helm release is confirmed.
