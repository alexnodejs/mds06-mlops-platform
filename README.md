# MLOps CI/CD 2.0 — практика до Тем 5–10

Один кластер, один репозиторій, шість занять. Від порожнього AWS-акаунта до
пайплайну, який сам тренує модель на кожен пуш і сам вирішує, пускати її в прод.

```
                        ┌─────────────────── AWS ───────────────────┐
   git push ──► GitHub  │                                           │
                Actions ─┼──► Step Functions ──► Job у EKS ──► MLflow│   Тема 10
                        │         │                                 │
                        │      quality gate: краща за чинну?        │
                        │      так ──► @champion ──► сервіс моделі  │
                        └───────────────────────────────────────────┘
                                            ▲
   ArgoCD ◄── Git ──────────────────────────┘                          Теми 6-9
   (усе, що працює в кластері, описано в цьому репозиторії)
```

---

## Мапа занять

| Тема | Про що | Що зʼявляється в кластері | Одна команда | Гайд |
|---|---|---|---|---|
| **5** | Kubernetes і EKS з нуля через Terraform | VPC, EKS, ноди, EBS CSI | `make cluster-up` | [docs/05](docs/05-eks-terraform.md) |
| **6** | Чотири способи деплою | nginx у чотирьох варіантах | вручну, з гайду | [docs/06](docs/06-deploy-methods.md) |
| **8** | Моніторинг ML-моделі | Prometheus, Grafana, Loki, модель | `make up` | [docs/08](docs/08-monitoring.md) |
| **9** | MLflow, реєстр моделей, дріфт | MLflow, MinIO, PostgreSQL, дріфт-експортер | `make up` | [docs/09](docs/09-mlflow-drift.md) |
| **10** | Автоматизоване тренування | Step Functions, Lambda, GitHub OIDC | `make pipeline-up` | [docs/10](docs/10-automated-training.md) |
| **11** | Управління моделями: реєстр, дані, blue-green | датасети в MinIO, теги версій, тіньовий варіант | `make seed` | [docs/11](docs/11-model-registry.md) |
| | ↳ сценарій демо Blue-Green | | `make bluegreen-up` | [docs/11-demo](docs/11-blue-green-demo.md) |

Теми 8 і 9 піднімаються однією командою `make up`, бо це один стек: модель без
моніторингу нецікава, а моніторинг без моделі нічого не показує.

---

## Швидкий старт

```bash
git clone <ваш форк> && cd mds06-mlops-platform

make init REPO=https://github.com/ВИ/mds06-mlops-platform.git   # підставити свій акаунт
make cluster-up      # Тема 5:   ~15 хв, EKS з нуля
make images          # збірка трьох образів у ваш ECR, ~15 хв
make up              # Теми 8-9: увесь стек через ArgoCD, ~8 хв
make pipeline-up     # Тема 10:  Step Functions + Lambda + OIDC, ~1 хв
```

Далі `make ports` друкує таблицю з посиланнями й паролями.

> **`make init` обовʼязковий.** ArgoCD читає `repoURL` із файлів у Git — він не
> бачить ні вашого оточення, ні Makefile. Поки в маніфестах чужий репозиторій,
> ваш кластер синхронізуватиметься з ним, а не з вашим форком.

Повний список команд — `make help`.

---

## Структура

| Тека | Що всередині |
|---|---|
| `terraform/cluster/` | Тема 5: VPC + EKS + IAM для EBS CSI. Тут же локальний `terraform.tfstate` |
| `terraform/training-pipeline/` | Тема 10: Lambda, Step Functions, IAM, OIDC для GitHub, Access Entry |
| `deploy/` | Тема 6: ті самі ресурси чотирма способами — `1-kubectl`, `2-helm`, `3-kustomize`, `4-argocd` |
| `apps/` | Вихідний код і Dockerfile: `model-api`, `trainer`, `drift-exporter`, `react-gitops` |
| `k8s/` | Маніфести, які синхронізує ArgoCD. Одна тека — один Application |
| `argocd/` | `root.yaml` (app-of-apps) і `apps/` з дочірніми Application |
| `lambdas/` | Три Lambda Теми 10: `validate`, `evaluate`, `log_metrics` |
| `k8s/trainer/` | Job-и, які запускають ПОДІЄЮ, а не GitOps: тренування, сейдинг даних, промоція |
| `scripts/` | Те, що викликає Makefile. Читати не обовʼязково, але корисно |
| `docs/` | Гайди по темах, вправи, розбір типових помилок |

**Головний принцип:** усе, що працює в кластері, описано в `k8s/` і застосовується
через ArgoCD. Виняток рівно один — `Secret` з паролями, він створюється командою
`make up` і в Git не потрапляє ніколи.

---

## Доступ до сервісів

`make ports` піднімає тунелі й друкує актуальну таблицю з паролями. Коротко:

| Сервіс | Адреса | Логін |
|---|---|---|
| MLflow | http://localhost:5001 | не потрібен |
| Grafana | http://localhost:3001 | `admin` / `admin` |
| ArgoCD | https://localhost:8080 (саме https) | `admin` / друкує `make ports` |
| Модель | http://localhost:8000 | `POST /predict` |
| MinIO | http://localhost:9001 | `minioadmin` / друкує `make ports` |
| Prometheus | http://localhost:9090 | не потрібен |
| React (демо GitOps) | http://localhost:8087 | не потрібен |

> MLflow на **5001**, а не 5000: порт 5000 на macOS тримає AirPlay Receiver, і
> тунель туди мовчки не встає.

---

## Скільки це коштує

| | за годину | за добу |
|---|---|---|
| EKS control plane | $0.10 | $2.40 |
| 3 × t3.medium | $0.125 | $3.00 |
| NAT Gateway | $0.05 + трафік | $1.30 |
| **Разом** | **≈ $0.29** | **≈ $7** |

Lambda і Step Functions Теми 10 у цих числах не видно: кілька десятків запусків
на місяць лишаються в безкоштовному ліміті.

Кластер платний **увесь час, поки існує**, незалежно від навантаження. Тому:

```bash
make down           # знести стек, лишити кластер   (~$7/добу далі йде)
make cluster-down   # знести все                    (рахунок у нуль)
```

⚠️ **Порядок важливий.** `make down` перед `make cluster-down`, інакше
LoadBalancer, створені Kubernetes, лишаться в AWS і заблокують видалення VPC —
`terraform destroy` зависне на `DependencyViolation`.

---

## Коли зламалось

Спершу — [docs/troubleshooting.md](docs/troubleshooting.md): там зібрані помилки,
на які цей матеріал уже наступав, із симптомом і причиною.

Швидка діагностика:

```bash
make status      # ноди, Application, поди, PVC, тунелі
```

⚠️ Дивіться колонку **READY**, а не тільки STATUS. Под зі статусом `Running` і
`READY 1/3` — це не робочий под, це под із двома вбитими по OOM сайдкарами.
Найдорожча година цього курсу пішла саме на цю різницю.

---

## Вправи

[docs/exercises.md](docs/exercises.md) — завдання, які можна давати студентам
самостійно: зламати й полагодити, зімітувати дріфт, відкотити модель, змусити
quality gate відхилити свідомо гіршу модель.
