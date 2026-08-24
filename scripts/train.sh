#!/usr/bin/env bash
# Тренування в кластері як Kubernetes Job; результат — у MLflow.
#
#   make train                         сітка за замовчуванням (6 запусків)
#   make train N=300,500 D=3,5         свої гіперпараметри
#   EXPERIMENT=my-test make train      в окремий експеримент
#
# Job збирається з ОДНОГО канонічного маніфеста k8s/trainer/job.yaml через
# envsubst. Раніше цей самий Job складався тут і в deploy-all.sh хірургією
# над YAML у python — два джерела правди на один об'єкт.

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

export JOB_NAME="train-$(date +%H%M%S)"
export EXPERIMENT="${EXPERIMENT:-iris-rf}"
export GRID_N_ESTIMATORS="${N:-50,100,200}"
export GRID_MAX_DEPTH="${D:-2,none}"
# Ручний запуск одразу ставить @champion. Пайплайн Теми 10 передає false і
# вирішує це сам після порівняння метрик.
export PROMOTE_TO_CHAMPION="${PROMOTE:-true}"
# Тема 11: на якому датасеті вчимось і хто це запустив.
#   make train DATASET=v3   -> перетренувати на зсунутих даних
export DATASET_URI="${DATASET_URI:-s3://datasets/iris/${DATASET:-v2}.csv}"
# Коміт беремо з git, а не з повітря: саме він потрапляє в тег git_sha і
# відповідає на питання «з якого коду ця модель».
export GIT_SHA="${GIT_SHA:-$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo "")}"
export TRAINED_BY="${TRAINED_BY:-$(whoami 2>/dev/null || echo manual)}"
export TRAINER_IMAGE="${TRAINER_IMAGE:-$(
  aws sts get-caller-identity --query Account --output text 2>/dev/null
).dkr.ecr.eu-central-1.amazonaws.com/mds06-mlflow-tools:v4}"

echo "── $JOB_NAME ──"
echo "   експеримент:  $EXPERIMENT"
echo "   n_estimators: $GRID_N_ESTIMATORS   max_depth: $GRID_MAX_DEPTH"
echo "   промоція:     $PROMOTE_TO_CHAMPION"
echo "   датасет:      $DATASET_URI"

envsubst < "$HERE/k8s/trainer/job.yaml" | kubectl apply -f - >/dev/null || exit 1

echo "── чекаю ──"
for i in $(seq 1 60); do
  sleep 5
  ok=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.succeeded}' 2>/dev/null)
  bad=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.failed}' 2>/dev/null)
  if [[ "$ok" == "1" ]]; then
    echo "   ✅ завершено за ~$((i*5))с"
    break
  fi
  if [[ -n "$bad" && "$bad" != "0" ]]; then
    echo "   ❌ впало:"
    kubectl logs -n mlflow -l job-name="$JOB_NAME" --tail=20
    exit 1
  fi
  if [[ $i -eq 60 ]]; then
    echo "   ⚠️  5 хв без результату; логи:"
    kubectl logs -n mlflow -l job-name="$JOB_NAME" --tail=20
    exit 1
  fi
done

echo
# Підсумок друкує окремий скрипт, а не python3 -c: у рядку всередині лапок
# bash вкладені лапки доводиться екранувати, і саме на цьому попередня версія
# падала з SyntaxError ПІСЛЯ успішного тренування — модель у реєстрі, а
# студент бачить трейсбек.
kubectl logs -n mlflow -l job-name="$JOB_NAME" 2>/dev/null \
  | python3 "$HERE/scripts/_summary.py"

echo
echo "   MLflow: http://localhost:5001/#/models/iris-rf"
