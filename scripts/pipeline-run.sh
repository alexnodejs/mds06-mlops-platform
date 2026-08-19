#!/usr/bin/env bash
# Запускає пайплайн Теми 10 з термінала — те саме, що робить GitHub Actions,
# але без пуша в репозиторій. Зручно на занятті: показати, як gate ВІДХИЛЯЄ
# свідомо гіршу модель, не роблячи для цього комітів.
#
#   make pipeline-run                      сітка за замовчуванням
#   make pipeline-run N=10 D=1             свідомо погана модель -> ВІДХИЛЕНО
#   make pipeline-run N=300,500 D=none     свідомо краща -> ПРОМОУТ
#
# Різниця з GitHub Actions лише в тому, ЯК ми отримали креденшели: тут —
# локальний профіль AWS, там — OIDC-токен. Сама state machine та сама.

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
REGION="${AWS_REGION:-eu-central-1}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

ARN=$(cd "$HERE/terraform/training-pipeline" && terraform output -raw state_machine_arn 2>/dev/null)
[[ -n "$ARN" ]] || { echo "❌ state machine не знайдена. Спершу: make pipeline-up"; exit 1; }

# Пайплайн вимагає справжній git SHA — так у CloudWatch і в MLflow видно,
# на якому саме коді натреновано модель.
SHA=$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo "0000000000000000000000000000000000000000")
NAME="cli-$(date +%Y%m%d-%H%M%S)"

INPUT=$(python3 -c "
import json,sys
print(json.dumps({
  'commit_sha': sys.argv[1],
  'ref': 'local',
  'run_url': '',
  'n_estimators': sys.argv[2],
  'max_depth': sys.argv[3],
  'experiment': sys.argv[4],
}))" "$SHA" "${N:-50,100,200}" "${D:-2,none}" "${EXPERIMENT:-iris-rf}")

echo "── запускаю $NAME ──"
echo "   $INPUT"
EXEC=$(aws stepfunctions start-execution --state-machine-arn "$ARN" \
        --name "$NAME" --input "$INPUT" --query executionArn --output text) || exit 1
echo "   граф: https://$REGION.console.aws.amazon.com/states/home?region=$REGION#/v2/executions/details/$EXEC"
echo

PREV=""
while true; do
  STATUS=$(aws stepfunctions describe-execution --execution-arn "$EXEC" --query status --output text)
  case "$STATUS" in
    RUNNING|PENDING_REDRIVE)
      # Показуємо, на якому кроці зараз, — інакше 2 хвилини тиші виглядають як зависання.
      STEP=$(aws stepfunctions get-execution-history --execution-arn "$EXEC" \
              --reverse-order --max-items 20 --query \
              'events[?type==`TaskStateEntered` || type==`ChoiceStateEntered`].stateEnteredEventDetails.name' \
              --output text 2>/dev/null | head -1)
      [[ "$STEP" != "$PREV" && -n "$STEP" ]] && { echo "   ▸ $STEP"; PREV="$STEP"; }
      sleep 5 ;;
    SUCCEEDED)
      echo
      aws stepfunctions describe-execution --execution-arn "$EXEC" --query output --output text \
        | python3 "$HERE/scripts/_pipeline_summary.py"
      echo
      echo "   MLflow:  http://localhost:5001/#/models/iris-rf"
      echo "   Модель:  http://localhost:8000"
      exit 0 ;;
    *)
      echo "   ❌ $STATUS"
      aws stepfunctions describe-execution --execution-arn "$EXEC" --query '[error,cause]' --output text
      exit 1 ;;
  esac
done
