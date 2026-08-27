#!/usr/bin/env bash
# Піднімає тунелі до всіх сервісів і друкує таблицю з логінами.
# Викликається як `make ports` і в кінці `make up`.
#
#   ./ports.sh          підняти + таблиця
#   ./ports.sh --stop   зупинити всі
#   ./ports.sh --table  лише таблиця, тунелі не чіпати

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
PWFILE="$HOME/.mlflow-demo-credentials"
# Порт Grafana виніс у змінну: 3000 — найпопулярніший порт для локальної
# розробки (Node, Next.js, Vite), і зайнятий він буває частіше, ніж вільний.
# Гірше, що конфлікт ТИХИЙ: kubectl слухає 127.0.0.1:3000, чужий процес — [::1]:3000,
# обидва стартують без помилки, а браузер на localhost віддає перевагу IPv6 і
# показує чужий застосунок замість Grafana.
#   GRAFANA_PORT=3005 make ports
GRAFANA_PORT="${GRAFANA_PORT:-3001}"

if [[ "${1:-}" == "--stop" ]]; then
  pkill -f "kubectl port-forward" 2>/dev/null && echo "✅ тунелі зупинено" || echo "нічого не працювало"
  exit 0
fi

pf() {
  kubectl get svc -n "$1" "$2" >/dev/null 2>&1 || { echo "  ⏭  $5 — сервісу немає"; return; }
  nohup kubectl port-forward -n "$1" "svc/$2" "$3:$4" > "/tmp/pf-$3.log" 2>&1 &
  disown
}

if [[ "${1:-}" != "--table" ]]; then
  pkill -f "kubectl port-forward" 2>/dev/null
  sleep 1
  # ⚠️ MLflow на 5001, а НЕ 5000: порти 5000 і 7000 на macOS тримає
  #    AirPlay Receiver, і тунель туди мовчки не стає (curl отримує
  #    403 від AirTunes). Це не помилка Kubernetes.
  pf mlflow     mlflow                                5001 80    "MLflow"
  pf mlflow     minio-console                         9001 9001  "MinIO"
  pf mlflow     drift-exporter                        9101 9100  "Drift"
  pf monitoring monitoring-grafana         "$GRAFANA_PORT" 80    "Grafana"
  pf monitoring monitoring-kube-prometheus-prometheus 9090 9090  "Prometheus"
  pf argocd     argocd-server                         8080 443   "ArgoCD"
  pf ml-demo    ml-model                              8000 80    "ML-модель"
  pf logging    loki                                  3100 3100  "Loki"
  pf demo-react react-app                             8087 80    "React (GitOps)"
  sleep 9
fi

MINIO_PW="(файл $PWFILE не знайдено)"
[[ -f "$PWFILE" ]] && { source "$PWFILE"; MINIO_PW="$MINIO_PW"; }
ARGO_PW=$(kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || echo "?")

st() { # порт -> ✅/❌
  local c
  if [[ "$1" == "8080" ]]; then c=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 6 "https://localhost:8080/healthz" 2>/dev/null)
  else c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 "http://localhost:$1$2" 2>/dev/null); fi
  [[ "$c" == "000" ]] && echo "❌" || echo "✅"
}

cat <<EOF

╔══════════════════════════════════════════════════════════════════════════════╗
║                     СЕРВІСИ — ТЕМИ 6-10                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝

  $(st 5001 /health) MLflow        http://localhost:5001              логін не потрібен
      ⚠️  Одразу відкривайте з режимом Machine Learning, інакше UI порожній:
          http://localhost:5001/#/experiments/1?workflowType=machine_learning

  $(st 9001 /) MinIO         http://localhost:9001              minioadmin / $MINIO_PW

  $(st "$GRAFANA_PORT" /api/health) Grafana       http://localhost:$GRAFANA_PORT              admin / admin
      Дашборди: «ML-модель — моніторинг» і «Якість моделі — дріфт»

  $(st 9090 /-/ready) Prometheus    http://localhost:9090              логін не потрібен

  $(st 8080 /healthz) ArgoCD        https://localhost:8080  ⚠️ https   admin / $ARGO_PW

  $(st 9101 /metrics) Дріфт         http://localhost:9101/metrics      сирі метрики
  $(st 8000 /healthz) ML-модель     http://localhost:8000              POST /predict
  $(st 3100 /ready) Loki          http://localhost:3100              лише API, UI у Grafana
  $(st 8087 /) React         http://localhost:8087              демо GitOps, Тема 6

──────────────────────────────────────────────────────────────────────────────
  ТЕМА 10 — тунелю не потребує, усе в консолі AWS
    make pipeline-run                    запустити тренування пайплайном
    make pipeline-run N=10 D=1           свідомо гірша модель -> ВІДХИЛЕНО
    Граф виконань: https://eu-central-1.console.aws.amazon.com/states/home

  ДЕМО ДРІФТУ
    kubectl -n ml-demo set env deploy/load-generator DRIFT_SHIFT=0.8   # увімкнути
    kubectl -n ml-demo set env deploy/load-generator DRIFT_SHIFT=0.0   # вимкнути
    Реакція ~2 хв: вікно має накопитись.

  ПЕРЕВІРКА МОДЕЛІ
    curl -X POST localhost:8000/predict -H 'Content-Type: application/json' \\
      -d '{"sepal_length":5.1,"sepal_width":3.5,"petal_length":1.4,"petal_width":0.2}'
──────────────────────────────────────────────────────────────────────────────
EOF
