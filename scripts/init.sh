#!/usr/bin/env bash
# Робить репозиторій ВАШИМ: підставляє ваш AWS-акаунт і ваш GitHub-репозиторій.
#   make init REPO=https://github.com/ВИ/mds06-mlops-platform.git
#
# Чому це окремий крок, а не змінні: ArgoCD читає repoURL з файлів у Git —
# він не бачить ні вашого оточення, ні Makefile. Значення мусить бути
# закомічене. Те саме з тегами образів у kustomization.yaml.
set -euo pipefail
NEW_REPO="$1"; NEW_ACCOUNT="${2:-}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"

OLD_REPO="https://github.com/alexnodejs/mds06-mlops-platform.git"
OLD_ACCOUNT="832828869208"

[[ -n "$NEW_ACCOUNT" ]] || { echo "❌ не вдалось визначити AWS-акаунт — перевірте aws sts get-caller-identity"; exit 1; }

echo "── репозиторій: $OLD_REPO"
echo "               -> $NEW_REPO"
grep -rl "$OLD_REPO" --include='*.yaml' --include='*.yml' --include='*.md' . \
  | xargs -r sed -i '' "s|$OLD_REPO|$NEW_REPO|g"

if [[ "$NEW_ACCOUNT" != "$OLD_ACCOUNT" ]]; then
  echo "── AWS-акаунт: $OLD_ACCOUNT -> $NEW_ACCOUNT"
  grep -rl "$OLD_ACCOUNT" --include='*.yaml' --include='*.yml' --include='*.tf' \
       --include='*.md' --include='*.sh' . \
    | xargs -r sed -i '' "s|$OLD_ACCOUNT|$NEW_ACCOUNT|g"
else
  echo "── AWS-акаунт уже ваш ($NEW_ACCOUNT), нічого не міняю"
fi

echo
echo "✅ готово. Перевірте зміни і закомітьте:"
echo "     git diff --stat && git commit -am 'init: свій акаунт і репозиторій'"
