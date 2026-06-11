{{/*
Expand the name of the chart.
*/}}
{{- define "snowmelt.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncated at 63 chars because Kubernetes name fields have that limit.
*/}}
{{- define "snowmelt.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label value: "<chart-name>-<chart-version>".
*/}}
{{- define "snowmelt.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "snowmelt.labels" -}}
helm.sh/chart: {{ include "snowmelt.chart" . }}
{{ include "snowmelt.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — used in matchLabels and service selectors.
*/}}
{{- define "snowmelt.selectorLabels" -}}
app.kubernetes.io/name: {{ include "snowmelt.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "snowmelt.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "snowmelt.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Headless service name — used as the StatefulSet serviceName and for
stable pod DNS: <pod>.<headless-svc>.<namespace>.svc.cluster.local
*/}}
{{- define "snowmelt.headlessServiceName" -}}
{{- printf "%s-headless" (include "snowmelt.fullname" .) }}
{{- end }}

{{/*
Resolved kube.namespace: use the explicitly configured value or fall back to
the release namespace.
*/}}
{{- define "snowmelt.kubeNamespace" -}}
{{- if .Values.config.kube.namespace }}
{{- .Values.config.kube.namespace }}
{{- else }}
{{- .Release.Namespace }}
{{- end }}
{{- end }}

{{/*
Resolved kube.service-name: use the explicitly configured value or fall back
to the headless service name.
*/}}
{{- define "snowmelt.kubeServiceName" -}}
{{- if (index .Values.config.kube "service-name") }}
{{- index .Values.config.kube "service-name" }}
{{- else }}
{{- include "snowmelt.headlessServiceName" . }}
{{- end }}
{{- end }}
