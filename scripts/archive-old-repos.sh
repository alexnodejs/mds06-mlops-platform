#!/usr/bin/env bash
# Прибирає ЛОКАЛЬНІ копії пʼяти репозиторіїв, які злились у цей монорепозиторій.
#
#   ./scripts/archive-old-repos.sh            показати, що буде зроблено
#   ./scripts/archive-old-repos.sh --do       зробити
#
# Не видаляє, а ПЕРЕНОСИТЬ у ~/Repos/goit/_archive-mds06/. Репозиторії на GitHub
# лишаються недоторканими — це друга страховка.
#
# ⚠️ Запускати ЛИШЕ після того, як новий репозиторій перевірено наживо:
#      make up && make pipeline-run
# У kuber-cluster-from-scratch лежить друга копія terraform.tfstate живого
# кластера. Поки не переконались, що стейт у новому місці робочий, ця копія —
# єдиний шлях назад.

set -euo pipefail
G="$HOME/Repos/goit"
ARCHIVE="$G/_archive-mds06"
OLD=(mds06-kuber-from-scratch mds06-ml-monitoring mds06-mlflow-drift
     mds06-react-gitops kuber-cluster-from-scratch)

DO=false
[[ "${1:-}" == "--do" ]] && DO=true

echo "── перевірка, що новий репозиторій справді робочий ──"
NEW="$G/mds06-mlops-platform"
[[ -f "$NEW/terraform/cluster/terraform.tfstate" ]] || { echo "❌ немає стейту в новому місці"; exit 1; }
N=$(python3 -c "import json;print(len(json.load(open('$NEW/terraform/cluster/terraform.tfstate'))['resources']))")
[[ "$N" -ge 50 ]] || { echo "❌ у стейті лише $N ресурсів — очікували 50+"; exit 1; }
echo "  ✅ стейт на місці: $N ресурсів"
git -C "$NEW" remote get-url origin >/dev/null 2>&1 || { echo "❌ новий репозиторій не запушено на GitHub"; exit 1; }
echo "  ✅ origin: $(git -C "$NEW" remote get-url origin)"

echo
for r in "${OLD[@]}"; do
  [[ -d "$G/$r" ]] || { echo "  ⏭  $r — уже немає"; continue; }
  SZ=$(du -sh "$G/$r" | cut -f1)
  UNPUSHED=$(git -C "$G/$r" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  echo "  $r  ($SZ, незакомічених змін: $UNPUSHED)"
  if $DO; then
    mkdir -p "$ARCHIVE"
    mv "$G/$r" "$ARCHIVE/"
    echo "      -> $ARCHIVE/$r"
  fi
done

echo
if $DO; then
  echo "✅ перенесено в $ARCHIVE"
  echo "   Репозиторії на GitHub не чіпав. Якщо все добре — заархівуйте їх там:"
  for r in "${OLD[@]}"; do
    [[ "$r" == kuber-cluster-from-scratch ]] && continue
    echo "     gh repo archive alexnodejs/$r --yes"
  done
else
  echo "Це був сухий прогін. Щоб зробити насправді: $0 --do"
fi
