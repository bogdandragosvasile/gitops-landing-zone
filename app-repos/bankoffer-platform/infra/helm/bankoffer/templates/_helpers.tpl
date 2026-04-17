{{/*
Expand the chart name.
*/}}
{{- define "bankoffer.name" -}}
{{- default .Chart.Name .Values.api.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a fully qualified release name (used as a base for all resources).
*/}}
{{- define "bankoffer.fullname" -}}
{{- if .Values.api.fullnameOverride }}
{{- .Values.api.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.api.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label (name + version).
*/}}
{{- define "bankoffer.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "bankoffer.labels" -}}
helm.sh/chart: {{ include "bankoffer.chart" . }}
{{ include "bankoffer.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — used by Deployments, Services, HPAs.
Pass the component name as the second argument via a dict:
  include "bankoffer.selectorLabels" (dict "Values" .Values "Chart" .Chart "Release" .Release "component" "api")
Or for the default component (api):
  include "bankoffer.selectorLabels" .
*/}}
{{- define "bankoffer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "bankoffer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .component }}
app.kubernetes.io/component: {{ .component }}
{{- end }}
{{- end }}

{{/*
ServiceAccount name for the API pod.
*/}}
{{- define "bankoffer.serviceAccountName" -}}
{{- if .Values.api.serviceAccount.create }}
{{- default (printf "%s-api" (include "bankoffer.fullname" .)) .Values.api.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.api.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
API image reference: repository:tag (tag falls back to appVersion).
*/}}
{{- define "bankoffer.api.image" -}}
{{- printf "%s:%s" .Values.api.image.repository (default .Chart.AppVersion .Values.api.image.tag) }}
{{- end }}

{{/*
Seed image reference.
*/}}
{{- define "bankoffer.seed.image" -}}
{{- printf "%s:%s" .Values.seed.image.repository (default .Chart.AppVersion .Values.seed.image.tag) }}
{{- end }}

{{/*
PostgreSQL service hostname (bitnami sub-chart naming convention).
*/}}
{{- define "bankoffer.postgresql.host" -}}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}

{{/*
Redis service hostname (bitnami standalone uses -redis-master).
*/}}
{{- define "bankoffer.redis.host" -}}
{{- printf "%s-redis-master" .Release.Name }}
{{- end }}

{{/*
Keycloak service hostname.
*/}}
{{- define "bankoffer.keycloak.host" -}}
{{- printf "%s-keycloak" .Release.Name }}
{{- end }}

{{/*
Keycloak internal URL for the API.
*/}}
{{- define "bankoffer.keycloak.url" -}}
{{- printf "http://%s:80/auth" (include "bankoffer.keycloak.host" .) }}
{{- end }}
