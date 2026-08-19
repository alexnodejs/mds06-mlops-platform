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
export TRAINER_IMAGE="${TRAINER_IMAGE:-$(
  aws sts get-caller-identity --query Account --output text 2>/dev/null
).dkr.ecr.eu-central-1.amazonaws.com/mds06-mlflow-tools:v2}"

echo "── $JOB_NAME ──"
echo "   експеримент:  $EXPERIMENT"
echo "   n_estimators: $GRID_N_ESTIMATORS   max_depth: $GRID_MAX_DEPTH"
echo "   промоція:     $PROMOTE_TO_CHAMPION"

envsubst < "$HERE/k8s/trainer/job.yaml" | kubectl apply -f - >/dev/null || exit 1

echo "── чекаю ──"
for i in $(seq 1 60); do
  sleep 5
  s=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.succeeded}' 2>/dev/null)
  f=$(kubectl get job "$JOB_NAME" -n mlflow -o jsonpath='{.status.failed}'    2>/dev/null)
  [[ "$s" == "1" ]] && { echo "   ✅ завершено за ~$((i*5))с"; break; }
  [[ "$f" != "" && "$f" != "0" ]] && {
    echo "   ❌ впало:"; kubectl logs -n mlflow -l job-name="$JOB_NAME" --tail=20; exit 1; }
  [[ $i -eq 60 ]] && { echo "   ⚠️  5 хв без результату"; exit 1; }
done

echo
kubectl logs -n mlflow -l job-name="$JOB_NAME" 2>/dev/null \
  | grep -E '"event": "(run_finished|best_run|registered)"' \
  | python3 -c '
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    if d["event"] == "run_finished":
        p = d["params"]
        print(f"   n={p[\"n_estimators\"]:>4} depth={str(p[\"max_depth\"]):>5}  "
              f"accuracy={d[\"accuracy\"]:.4f}  f1={d[\"f1\"]:.4f}")
    elif d["event"] == "best_run":
        print(f"   ⭐ найкращий: f1={d[\"f1\"]:.4f}")
    elif d["event"] == "registered":
        alias = d.get("alias") or "без аліаса (рішення за quality gate)"
        print(f"   📦 у реєстрі: {d[\"model\"]} v{d[\"version\"]} — {alias}")
'
echo
echo "   MLflow: http://localhost:5001/#/experiments?workflowType=machine_learning"
