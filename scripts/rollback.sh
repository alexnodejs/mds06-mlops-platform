#!/usr/bin/env bash
# Відкат моделі на попередню версію. Тема 11, слайд 14.
#
#   make rollback              повернути @champion на @previous
#   make rollback VERSION=3    повернути на конкретну версію
#
# Працює через той самий promote.py, що й промоція: відкат — це не окремий
# механізм, а та сама операція «перевісити аліас», лише в інший бік. Саме тому
# у скрипті немає жодної власної логіки роботи з реєстром.

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

kubectl get ns mlflow >/dev/null 2>&1 || { echo "❌ немає namespace mlflow — спершу make up"; exit 1; }

TARGET="${VERSION:-}"
if [[ -z "$TARGET" ]]; then
  # Питаємо MLflow, куди вказує @previous. Через тунель, а не з пода: так
  # видно помилку одразу, а не в логах Job-а.
  TARGET=$(curl -s "http://localhost:5001/api/2.0/mlflow/registered-models/alias?name=iris-rf&alias=previous" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("model_version",{}).get("version",""))' 2>/dev/null)
fi

if [[ -z "$TARGET" ]]; then
  cat <<'EOF'
❌ Не знайшов, куди відкочуватись.

   Аліас @previous зʼявляється лише ПІСЛЯ першої заміни чемпіона: поки в
   реєстрі був один champion, попередника не існує — і це не помилка.

   Перевірте тунель (make ports) і подивіться, що є:
     curl -s localhost:5001/api/2.0/mlflow/registered-models/get?name=iris-rf | python3 -m json.tool

   Або вкажіть версію явно:  make rollback VERSION=3
EOF
  exit 1
fi

echo "── відкочую @champion на версію $TARGET ──"
export JOB_NAME="rollback-$(date +%H%M%S)"
export MODEL_VERSION="$TARGET"
# envsubst не має значень за замовчуванням — задаємо тут.
export MODEL_ALIAS="${MODEL_ALIAS:-champion}"
export TRAINER_IMAGE="${TRAINER_IMAGE:-$(
  aws sts get-caller-identity --query Account --output text 2>/dev/null
).dkr.ecr.eu-central-1.amazonaws.com/mds06-mlflow-tools:v5}"

envsubst < "$HERE/k8s/trainer/promote-job.yaml" | kubectl apply -f - >/dev/null || exit 1

for i in $(seq 1 30); do
  sleep 4
  ok=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.succeeded}' 2>/dev/null)
  bad=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.failed}' 2>/dev/null)
  [[ "$ok" == "1" ]] && { kubectl logs -n mlflow -l job-name="$JOB_NAME" 2>/dev/null | sed 's/^/   /'; break; }
  [[ -n "$bad" && "$bad" != "0" ]] && { echo "   ❌ впало:"; kubectl logs -n mlflow -l job-name="$JOB_NAME" --tail=20; exit 1; }
  [[ $i -eq 30 ]] && { echo "   ⚠️  2 хв без результату"; exit 1; }
done

echo
echo "   Перевірка (сервіс перечитує реєстр за ~30 c, /reload прискорює):"
echo "     curl -s localhost:8000/healthz | python3 -m json.tool"
