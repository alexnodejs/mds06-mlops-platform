# ══════════════════════════════════════════════════════════════════════════
# ОДНА ТОЧКА ВХОДУ В УВЕСЬ КУРС
#
# Раніше було п'ять скриптів у чотирьох репозиторіях, і треба було памʼятати,
# який із них що піднімає. Тепер: `make help`.
# ══════════════════════════════════════════════════════════════════════════
SHELL       := /usr/bin/env bash
AWS_PROFILE ?= goit-aws-mds
AWS_REGION  ?= eu-central-1
export AWS_PROFILE AWS_REGION

# Реєстр ECR визначається з ваших креденшелів, а не зашитий у Makefile.
ACCOUNT  = $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
REGISTRY = $(ACCOUNT).dkr.ecr.$(AWS_REGION).amazonaws.com

.DEFAULT_GOAL := help
.PHONY: help init cluster-up cluster-down images login up down ports status train \
        loadgen pipeline-up pipeline-run pipeline-down clean

help: ## показати цю довідку
	@echo ""
	@echo "  MLOps CI/CD 2.0 — практика до Тем 5-10"
	@echo ""
	@grep -E '^[a-z0-9-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Типовий шлях з нуля:"
	@echo "    make init REPO=https://github.com/ВИ/mds06-mlops-platform.git"
	@echo "    make cluster-up     # Тема 5:  ~15 хв, EKS з нуля"
	@echo "    make images         # збірка образів у ваш ECR"
	@echo "    make up             # Теми 6,8,9: увесь стек через ArgoCD"
	@echo "    make pipeline-up    # Тема 10: Step Functions + GitHub OIDC"
	@echo ""

# ── Підготовка ────────────────────────────────────────────────────────────
init: ## підставити СВІЙ AWS-акаунт і СВІЙ репозиторій (робиться один раз)
	@test -n "$(REPO)" || { echo "потрібно: make init REPO=https://github.com/ВИ/РЕПО.git"; exit 1; }
	@bash scripts/init.sh "$(REPO)" "$(ACCOUNT)"

login: ## docker login у ваш ECR
	aws ecr get-login-password --region $(AWS_REGION) \
	  | docker login --username AWS --password-stdin $(REGISTRY)

images: login ## зібрати й запушити всі три образи
	@bash scripts/build-images.sh "$(REGISTRY)"

# ── Тема 5: кластер ───────────────────────────────────────────────────────
cluster-up: ## Тема 5: підняти EKS (terraform apply, ~15 хв)
	cd terraform/cluster && terraform init -input=false && terraform apply
	aws eks update-kubeconfig --name mlops-demo --region $(AWS_REGION)
	kubectl apply -f deploy/0-storage/storageclass-gp3.yaml

cluster-down: ## Тема 5: знести ВЕСЬ кластер (спершу make down!)
	@echo "⚠️  це знищить кластер повністю. Спершу переконайтесь, що зробили make down,"
	@echo "   інакше LoadBalancer-и від Service лишаться і terraform не зможе видалити VPC."
	@read -p "   продовжити? [y/N] " a && [ "$$a" = "y" ]
	cd terraform/cluster && terraform destroy

# ── Теми 6, 8, 9: стек у кластері ─────────────────────────────────────────
up: ## підняти весь стек через ArgoCD + тунелі + таблиця сервісів
	@bash scripts/up.sh

down: ## знести стек (кластер і ArgoCD лишаються)
	@bash scripts/down.sh

ports: ## тунелі до всіх сервісів + таблиця з логінами
	@bash scripts/ports.sh

status: ## що зараз працює в кластері
	@bash scripts/status.sh

loadgen: ## увімкнути генератор трафіку (без нього графіки порожні)
	kubectl -n ml-demo scale deploy/load-generator --replicas=1

train: ## тренування вручну: make train [N=50,100 D=2,none]
	@bash scripts/train.sh

# ── Тема 10: автоматизоване тренування ────────────────────────────────────
pipeline-up: ## Тема 10: Lambda + Step Functions + GitHub OIDC (terraform apply)
	cd terraform/training-pipeline && terraform init -input=false && terraform apply

pipeline-run: ## Тема 10: запустити пайплайн вручну (те саме робить GitHub Actions)
	@bash scripts/pipeline-run.sh

pipeline-down: ## Тема 10: знести Lambda + Step Functions
	cd terraform/training-pipeline && terraform destroy

clean: ## зупинити всі тунелі
	@pkill -f "kubectl port-forward" 2>/dev/null && echo "тунелі зупинено" || echo "нічого не працювало"
