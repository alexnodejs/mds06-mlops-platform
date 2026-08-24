#!/usr/bin/env bash
# Збирає й пушить усі три образи в ECR. Викликається як `make images`.
#
# --platform linux/amd64 ОБОВʼЯЗКОВИЙ: мак розробника ARM, ноди EKS x86_64.
# Без нього под падає з "exec format error", і це виглядає як зламаний образ.
set -euo pipefail
REGISTRY="$1"
HERE="$(cd "$(dirname "$0")/.." && pwd)"; cd "$HERE"

build() { # <репозиторій> <тег> <шлях до Dockerfile> <контекст>
  local repo="$1" tag="$2" dockerfile="$3" ctx="$4"
  echo "── $repo:$tag ──"
  aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$repo" >/dev/null
  docker buildx build --platform linux/amd64 -f "$dockerfile" \
    -t "$REGISTRY/$repo:$tag" --push "$ctx"
}

# Контракт: ОДИН образ mds06-mlflow-tools містить і train.py, і drift_exporter.py.
# Контекст — корінь репозиторію, бо Dockerfile копіює з двох різних тек.
build mds06-mlflow-tools v3 apps/trainer/Dockerfile       .
build mds06-ml-model     v6 apps/model-api/Dockerfile     apps/model-api
build mds06-react-gitops v2 apps/react-gitops/Dockerfile  apps/react-gitops

echo
echo "✅ Теги в ECR мусять збігатися з newTag у k8s/*/kustomization.yaml."
