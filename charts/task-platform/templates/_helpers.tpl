{{/*
Object metadata labels. Keeps the existing `app: <name>` label and adds the
standard ones. Pod template and selector labels deliberately stay `app: <name>`
only: selectors are immutable, and any pod template change rolls the pods.
Usage: include "task-platform.labels" (dict "root" $ "app" "task-api" "component" "api")
*/}}
{{- define "task-platform.labels" -}}
app: {{ .app }}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/part-of: task-platform
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" }}
{{- end }}

{{- define "task-platform.appImage" -}}
{{ printf "%s:%s" .Values.image.repository (toString .Values.image.tag) }}
{{- end }}

{{- define "task-platform.redisImage" -}}
{{ printf "%s:%s" .Values.redis.image.repository (toString .Values.redis.image.tag) }}
{{- end }}

{{- define "task-platform.imageDataClaim" -}}
{{ .Values.persistence.imageData.existingClaim | default "image-data" }}
{{- end }}

{{- define "task-platform.redisDataClaim" -}}
{{ .Values.persistence.redisData.existingClaim | default "redis-data" }}
{{- end }}

{{/*
Environment shared by the API and the worker.
*/}}
{{- define "task-platform.appEnv" -}}
- name: REDIS_URL
  value: {{ printf "redis://redis:%d/0" (int .Values.redis.service.port) }}
- name: QUEUE_NAME
  value: {{ .Values.config.queueName | quote }}
- name: LEASE_SECONDS
  value: {{ .Values.config.leaseSeconds | quote }}
- name: STORAGE_DIR
  value: {{ .Values.config.storageDir | quote }}
{{- with .Values.config.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Init container that blocks app pods until the Redis Service answers PING.
*/}}
{{- define "task-platform.waitForRedis" -}}
- name: wait-for-redis
  image: {{ include "task-platform.redisImage" . }}
  imagePullPolicy: {{ .Values.redis.image.pullPolicy }}
  command: ["/bin/sh", "-c"]
  args:
    - until redis-cli -h redis -p {{ int .Values.redis.service.port }} ping >/dev/null 2>&1; do echo 'Waiting for Redis'; sleep 2; done
{{- end }}
