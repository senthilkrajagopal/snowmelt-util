{{/*
Common labels for resources this wrapper chart owns directly (as opposed to
anything rendered by the vendored kagent subchart, which has its own helpers).
*/}}
{{- define "snowmelt-kagent.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: kagent
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}
