#!/usr/bin/env bash
# Кладе навчальні датасети в MinIO. Викликається як `make seed` і кроком 4
# у scripts/up.sh.
#
# Ідемпотентний: put_object перезаписує об'єкт тим самим вмістом (генератор
# детермінований), тож повторний запуск нічого не ламає й нічого не дублює.

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

export JOB_NAME="seed-$(date +%H%M%S)"
# Дефолти дублюють seed_datasets.py навмисно: envsubst не має значень за
# замовчуванням, а порожній рядок у env перебив би дефолт у коді.
export NOISE_STD_FRAC="${NOISE:-0.5}"
export V3_LABEL_NOISE="${LABEL_NOISE:-0.20}"
export TRAINER_IMAGE="${TRAINER_IMAGE:-$(
  aws sts get-caller-identity --query Account --output text 2>/dev/null
).dkr.ecr.eu-central-1.amazonaws.com/mds06-mlflow-tools:v5}"

kubectl get ns mlflow >/dev/null 2>&1 || { echo "❌ немає namespace mlflow — спершу make up"; exit 1; }
kubectl -n mlflow get secret mlflow-credentials >/dev/null 2>&1 \
  || { echo "❌ немає секрету mlflow-credentials — спершу make up"; exit 1; }

echo "── $JOB_NAME ──"
envsubst < "$HERE/k8s/trainer/seed-job.yaml" | kubectl apply -f - >/dev/null || exit 1

for i in $(seq 1 30); do
  sleep 4
  ok=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.succeeded}' 2>/dev/null)
  bad=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.failed}' 2>/dev/null)
  if [[ "$ok" == "1" ]]; then
    kubectl logs -n mlflow -l job-name="$JOB_NAME" 2>/dev/null \
      | python3 "$HERE/scripts/_summary.py"
    echo "   ✅ датасети на місці"
    exit 0
  fi
  if [[ -n "$bad" && "$bad" != "0" ]]; then
    echo "   ❌ впало:"; kubectl logs -n mlflow -l job-name="$JOB_NAME" --tail=20
    exit 1
  fi
done

echo "   ⚠️  2 хв без результату; логи:"
kubectl logs -n mlflow -l job-name="$JOB_NAME" --tail=20
exit 1
