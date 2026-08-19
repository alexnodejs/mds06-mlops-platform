{{/*
_helpers.tpl — файли, що починаються з підкреслення, Helm НЕ рендерить
у Kubernetes-ресурси. Тут живуть шматки шаблонів для повторного використання.

{{/* ... */}} — це коментар Helm: він зникає з результату.
*/}}

{{/*
Повне імʼя ресурсів. .Release.Name — це те, що ви написали в
`helm install <ЦЕ_ІМʼЯ> ./nginx-demo`, тому один чарт можна поставити
кілька разів під різними іменами і вони не зіткнуться.

Умова нижче — стандартна поведінка Helm: якщо імʼя релізу вже містить
імʼя чарту, не дублювати його. Інакше `helm install nginx-demo ./nginx-demo`
дало б негарне "nginx-demo-nginx-demo".

trunc 63 — обмеження Kubernetes на довжину імені ресурсу.
trimSuffix "-" — прибрати дефіс, якщо обрізання лишило його останнім символом.
*/}}
{{- define "nginx-demo.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Стандартний набір міток. Kubernetes рекомендує саме ці імена (app.kubernetes.io/*),
за ними працюють дашборди, kubectl і ArgoCD.
*/}}
{{- define "nginx-demo.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/*
Мітки для selector — ТІЛЬКИ незмінні.
Версію сюди класти НЕ можна: selector у Deployment незмінний після створення,
і при оновленні appVersion деплой впаде з помилкою "field is immutable".
*/}}
{{- define "nginx-demo.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
