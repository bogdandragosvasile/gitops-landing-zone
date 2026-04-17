{{/*
Expand the name of the chart.
*/}}
{{- define "pangolin-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "pangolin-stack.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "pangolin-stack.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "pangolin-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Secret name for newt credentials
*/}}
{{- define "pangolin-stack.newt.secretName" -}}
{{- if .Values.newt.credentials.existingSecret -}}
{{- .Values.newt.credentials.existingSecret }}
{{- else -}}
{{- include "pangolin-stack.fullname" . }}-newt-credentials
{{- end }}
{{- end }}

{{/*
Secret name for operator credentials
*/}}
{{- define "pangolin-stack.operator.secretName" -}}
{{- if .Values.operator.credentials.existingSecret -}}
{{- .Values.operator.credentials.existingSecret }}
{{- else -}}
{{- include "pangolin-stack.fullname" . }}-operator-credentials
{{- end }}
{{- end }}
