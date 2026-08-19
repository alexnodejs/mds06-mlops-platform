# Kubernetes з нуля через Terraform + tfenv

**Тема 5. Kubernetes — практична частина**
Курс MLOps CI/CD 2.0

Піднімаємо справжній кластер Kubernetes в AWS (EKS) за допомогою Terraform,
деплоїмо в нього застосунок, ламаємо його і дивимось, як Kubernetes сам себе
лікує. Наприкінці — акуратно все зносимо, щоб не платити.

> **Усе перевірено на реальному AWS 3 серпня 2026 року.**
> `terraform plan` дає **62 ресурси, 0 помилок**
> (58 у першій редакції + 4 за сховище — див. [6.5](#сховище-ebs-csi-driver-і-чому-дефолтний-gp2-мертвий)).

---

## Зміст

| # | Крок | Час |
|---|------|-----|
| 0 | [Що ми будуємо і скільки це коштує](#0-що-ми-будуємо-і-скільки-це-коштує) | 5 хв |
| 1 | [Передумови: AWS, aws-cli, kubectl](#1-передумови) | 10 хв |
| 2 | [tfenv — менеджер версій Terraform](#2-tfenv--менеджер-версій-terraform) | 5 хв |
| 3 | [Структура проєкту](#3-структура-проєкту) | 2 хв |
| 4 | [`versions.tf` — построково](#4-versionstf--построково) | 5 хв |
| 5 | [`variables.tf` — построково](#5-variablestf--построково) | 5 хв |
| 6 | [`main.tf` — построково](#6-maintf--построково) | 20 хв |
| 7 | [`outputs.tf` — построково](#7-outputstf--построково) | 3 хв |
| 8 | [Запуск: init → plan → apply](#8-запуск-init--plan--apply) | 20 хв |
| 9 | [Підключаємо kubectl](#9-підключаємо-kubectl) | 5 хв |
| 10 | [Деплой застосунку](#10-деплой-застосунку) | 10 хв |
| 11 | [kubectl і fault-tolerance наживо](#11-kubectl-і-fault-tolerance-наживо) | 15 хв |
| 12 | [Бонус: стейт у S3](#12-бонус-стейт-у-s3) | 10 хв |
| 13 | [🔴 Прибирання і контроль витрат](#13--прибирання-і-контроль-витрат) | 15 хв |
| 14 | [Типові помилки](#14-типові-помилки) | — |

---

## 0. Що ми будуємо і скільки це коштує

### Схема

Пригадаймо ієрархію зі слайда 9: **Кластер → Нода → Под → Контейнер**.

```
                        ВАШ НОУТБУК
                     terraform / kubectl
                             │
                             │ HTTPS
┌────────────────────────────┼─────────────────────────────────────┐
│  AWS  VPC 10.0.0.0/16      │                                     │
│                            ▼                                     │
│   ┌────────────────────────────────────────────┐                 │
│   │  CONTROL PLANE  (керує AWS — слайд 28)     │                 │
│   │  API Server │ Scheduler │ Controller Mgr │ etcd              │
│   └────────────────────────────────────────────┘                 │
│                            │                                     │
│   ┌── публічні сабнети ────┼───────────────────────────────┐     │
│   │   NAT Gateway          │        Load Balancer          │     │
│   └────────────────────────┼───────────────────────────────┘     │
│                            ▼                                     │
│   ┌── приватні сабнети ──────────────────────────────────────┐   │
│   │   WORKER NODE 1 (t3.medium)   WORKER NODE 2 (t3.medium)  │   │
│   │   kubelet │ containerd        kubelet │ containerd       │   │
│   │   kube-proxy                  kube-proxy                 │   │
│   │   ┌─────┐ ┌─────┐             ┌─────┐ ┌─────┐            │   │
│   │   │ Pod │ │ Pod │             │ Pod │ │ Pod │            │   │
│   │   └─────┘ └─────┘             └─────┘ └─────┘            │   │
│   └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

Усе всередині рамки створює **Terraform**, крім Load Balancer — його створить
**Kubernetes**, коли ми задеплоїмо застосунок. Запамʼятайте це, у розділі 13 воно
вистрелить.

### Скільки це коштує

Регіон `eu-central-1`, ціни станом на серпень 2026:

| Ресурс | Ціна | За годину |
|---|---|---|
| EKS control plane | $0.10 / год за кластер | **$0.100** |
| 3 × `t3.medium` (On-Demand) | $0.0456 / год кожен | **$0.137** |
| NAT Gateway | $0.052 / год + трафік | **$0.052** |
| Classic Load Balancer (зʼявиться в кроці 10) | $0.030 / год + трафік | **$0.030** |
| EBS-диски нод (3 × 20 GiB gp3) | ~$0.095 / GiB-міс | **$0.008** |
| | **РАЗОМ** | **≈ $0.33 / год** |

| Скільки протримати | Рахунок |
|---|---|
| Пара 3 години | **≈ $1.00** |
| Забули вимкнути на добу | ≈ $7.85 |
| Забули вимкнути на місяць | **≈ $235** 😱 |

> **Чому 3 ноди, а не 2.** `node_desired_size` за замовчуванням `3`, бо повний
> стек курсу (Теми 8–9) не влазить у 34 слоти подів на двох `t3.medium` —
> арифметика в коментарі до змінної у
> [`variables.tf`](../terraform/cluster/variables.tf). Якщо ви робите **лише**
> Тему 5, третя нода не потрібна: `terraform apply -var node_desired_size=2`,
> і рахунок падає до ≈ $0.28/год.

> ### 🔴 ГОЛОВНЕ ПРАВИЛО
> **`terraform destroy` наприкінці заняття — обовʼязково.**
> EKS **не входить** у безкоштовний рівень AWS. Кластер тарифікується
> щосекунди, навіть якщо в ньому нічого не запущено.
> Дійдіть до [розділу 13](#13--прибирання-і-контроль-витрат) — там чек-лист.

---

## 1. Передумови

### 1.1. Що має бути встановлено

```bash
# Перевіряємо, що є на машині
aws --version      # потрібно 2.x
kubectl version --client
brew --version     # macOS; на Linux — apt/dnf
```

Якщо чогось бракує (macOS):

```bash
brew install awscli kubectl
```

Версія `kubectl` має відрізнятись від версії кластера не більше ніж на одну
мінорну. Ми беремо Kubernetes **1.34**, отже `kubectl` 1.33–1.35 підійде.

### 1.2. AWS-акаунт і доступи

Потрібен IAM-користувач з правами створювати VPC, EKS, EC2 та IAM-ролі.
Для навчання найпростіше — політика `AdministratorAccess`.

**Створення ключів:** AWS Console → IAM → Users → ваш користувач →
Security credentials → Create access key → *Command Line Interface (CLI)*.

```bash
aws configure
# AWS Access Key ID:     AKIA................
# AWS Secret Access Key: ................................
# Default region name:   eu-central-1
# Default output format: json
```

**Перевірка — обовʼязкова:**

```bash
aws sts get-caller-identity
```

Має вивести ваш `Account` і `Arn`. Якщо бачите
`Unable to locate credentials` — ключі не збереглись, повторіть `aws configure`.

<details>
<summary><b>Якщо у вас кілька AWS-акаунтів (профілі)</b></summary>

Коли профілів багато і `default` серед них немає, кожна команда потребує
явного профілю. Найзручніше — виставити його один раз на всю сесію терміналу:

```bash
export AWS_PROFILE=назва-вашого-профілю
aws sts get-caller-identity          # перевірка
```

`terraform` підхоплює `AWS_PROFILE` автоматично — жодних змін у коді не треба.

Подивитись список профілів: `aws configure list-profiles`
</details>

### 1.3. Перевірка лімітів

За замовчуванням в акаунті ліміт **5 Elastic IP на регіон**, і наш NAT Gateway
займе один. Якщо в акаунті вже щось є, перевірте:

```bash
aws ec2 describe-addresses --query 'length(Addresses)' --region eu-central-1
```

---

## 2. tfenv — менеджер версій Terraform

### 2.1. Чому не `brew install terraform`

Спробуйте:

```bash
brew info terraform | head -3
```

Побачите **1.5.7** — версію з **серпня 2023 року**. Homebrew заморозив формулу,
коли HashiCorp змінила ліцензію Terraform з MPL на BSL. Тобто через `brew`
свіжий Terraform ви вже не поставите.

Друга причина важливіша за першу: **у різних проєктах різні версії Terraform**.
Проєкт з 2023-го хоче 1.5, новий — 1.15. Один глобальний бінарник цю задачу не
вирішує.

**tfenv** — це обгортка, яка тримає кілька версій Terraform поруч і перемикає
їх автоматично залежно від теки. Аналог `nvm` для Node.js чи `pyenv` для Python.

### 2.2. Встановлення

```bash
# ⚠️ КРИТИЧНО: спершу знести стару формулу terraform.
# tfenv і terraform обидва претендують на /opt/homebrew/bin/terraform,
# і brew відмовиться створювати симлінк:
#   Error: Cannot link tfenv... target /opt/homebrew/bin/terraform already exists
brew uninstall terraform

brew install tfenv
tfenv --version        # tfenv 3.2.2
```

<details>
<summary><b>Не macOS / без Homebrew</b></summary>

```bash
git clone --depth=1 https://github.com/tfutils/tfenv.git ~/.tfenv
echo 'export PATH="$HOME/.tfenv/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```
</details>

### 2.3. Ставимо потрібну версію

```bash
tfenv list-remote | head -5    # які версії взагалі існують
tfenv install 1.15.8           # завантажити конкретну
tfenv use 1.15.8               # зробити версією за замовчуванням
terraform version              # Terraform v1.15.8
```

tfenv завантажує архів з `releases.hashicorp.com` і **звіряє SHA256**, тож
підміну бінарника ви побачите одразу.

### 2.4. Файл `.terraform-version` — головна фішка

У теці `terraform/cluster/` лежить файл з єдиним рядком:

```
1.15.8
```

Завдяки йому **нікому не треба нічого памʼятати**. Студент клонує репозиторій і:

```bash
cd terraform/cluster
tfenv install    # без аргументу — прочитає .terraform-version
```

Далі, доки ви в цій теці, `terraform` **завжди** буде 1.15.8 — незалежно від того,
що виставлено глобально. Побачити це можна так:

```bash
cd terraform/cluster && terraform version   # 1.15.8
cd ../..             && terraform version   # ваша глобальна версія
```

При `tfenv use` він навіть попереджає:

```
tfenv: Default version file overridden by .../terraform/cluster/.terraform-version,
       changing the default version has no effect
```

### 2.5. Дві розумні команди

```bash
tfenv install min-required     # прочитати required_version з .tf і поставити мінімум
tfenv install latest-allowed   # ...поставити максимум, який дозволяє конфіг
```

Вони читають блок `required_version` з `versions.tf` — тому цей блок і має бути
чесно заповнений.

### Шпаргалка tfenv

| Команда | Що робить |
|---|---|
| `tfenv list` | які версії вже стоять локально |
| `tfenv list-remote` | які версії доступні для завантаження |
| `tfenv install 1.15.8` | поставити конкретну |
| `tfenv install` | поставити версію з `.terraform-version` |
| `tfenv use 1.15.8` | зробити версією за замовчуванням |
| `tfenv pin` | записати поточну версію у `.terraform-version` |
| `tfenv uninstall 1.5.7` | видалити версію |

---

## 3. Структура проєкту

Увесь курс (Теми 5–10) живе в **одному** репозиторії
`https://github.com/alexnodejs/mds06-mlops-platform.git`. До Теми 5 стосується
ось це:

```
mds06-mlops-platform/
├── README.md                  ← мапа занять і швидкий старт
├── Makefile                   ← одна точка входу: `make help`
├── .gitignore                 ← що НЕ комітити (найважливіше — *.tfstate)
├── terraform/
│   ├── cluster/               ← ТЕМА 5: усе, про що цей гайд
│   │   ├── .terraform-version ← 1.15.8, читає tfenv
│   │   ├── versions.tf        ← які версії Terraform і провайдерів
│   │   ├── variables.tf       ← «ручки», які можна крутити
│   │   ├── main.tf            ← що саме створюємо в AWS
│   │   ├── ebs-csi-iam.tf     ← IAM-роль для драйвера дисків (розділ 6.5)
│   │   └── outputs.tf         ← що показати в кінці
│   └── training-pipeline/     ← Тема 10, окремий стейт, окремий apply
├── deploy/
│   ├── 0-storage/             ← StorageClass gp3: це Kubernetes, не AWS,
│   │   ├── storageclass-gp3.yaml       тому kubectl, а не Terraform
│   │   └── smoke-test.yaml    ← 40-секундна перевірка, що диски справді дають
│   └── 1-kubectl/ 2-helm/ 3-kustomize/ 4-argocd/   ← Тема 6
├── apps/  k8s/  argocd/  lambdas/                  ← Теми 6, 8, 9, 10
├── scripts/                   ← те, що викликає Makefile
└── docs/                      ← ви тут: docs/05-eks-terraform.md
```

**Два окремі Terraform-проєкти, а не один.** У `terraform/cluster/` і
`terraform/training-pipeline/` свої `terraform.tfstate`, свої `init`/`apply`.
Так зроблено навмисно: пайплайн Теми 10 змінюють часто, а кластер — ні, і
зламаний `apply` пайплайна не має жодного шансу зачепити VPC чи node group.

**Чому файлів кілька, а не один?** Для Terraform це не має жодного значення —
він склеює **всі** `.tf`-файли в теці в один конфіг перед виконанням. Розбиття
існує виключно для людей. Розбиття на `versions/variables/main/outputs` —
галузевий стандарт.

**Порядок блоків теж не важить.** Terraform сам будує граф залежностей: побачив
`module.vpc.vpc_id` всередині `module "eks"` — отже VPC треба створити першою.
Тому й паралелить створення там, де залежностей немає.

---

## 4. `versions.tf` — построково

```hcl
terraform {
  required_version = "~> 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.52"
    }
  }
}
```

| Рядок | Що робить | Що буде, якщо прибрати |
|---|---|---|
| `terraform { }` | блок налаштувань самого Terraform (не хмари) | — |
| `required_version = "~> 1.15.0"` | діапазон версій бінарника: `>= 1.15.0, < 1.16.0` | на Terraform 1.5 конфіг мовчки запуститься і впаде посеред `apply` з незрозумілою помилкою |
| `required_providers { }` | список плагінів для завантаження | Terraform не зрозуміє слово `aws` |
| `source = "hashicorp/aws"` | скорочення для `registry.terraform.io/hashicorp/aws` | — |
| `version = "~> 6.52"` | пін версії провайдера | завтра вийде 7.0 з breaking changes і зламає ваш проєкт без жодних змін у коді |

### 🔴 Чому саме `~> 1.15.0`, а не `~> 1.15`

Третя цифра тут не педантизм, а захист від сценарію, який паралізує всю групу.

`~> 1.15` дозволяє **1.16 і 1.17**. Terraform при кожному `apply` піднімає поле
`version` усередині `terraform.tfstate` до версії того, хто цей `apply` зробив,
і **назад його не відкотити**. Один студент оновив бінарник, зробив `apply` —
і всі інші отримують:

```
Error: Unsupported state file format

state snapshot was created by Terraform v1.16.0, which is newer than current
v1.15.8; upgrade to Terraform v1.16.0 or greater to work with this state
```

Причому не тільки на `apply` — навіть `terraform plan` більше не працює.

`~> 1.15.0` = `>= 1.15.0, < 1.16.0`: патчі приймаємо, мінорний стрибок — ні.
Значення тримається синхронним із файлом `.terraform-version` (1.15.8), і саме
його читає `tfenv install min-required`.

### Оператори версій — головне, що треба знати

| Запис | Що дозволяє | Коли вживати |
|---|---|---|
| `= 6.52.0` | рівно цю версію | майже ніколи — заблокує баг-фікси |
| `>= 6.52` | цю і будь-яку новішу, включно з 7.0 | **небезпечно** — впустить breaking changes |
| `~> 6.52` | `>= 6.52, < 7.0` | ✅ провайдери і модулі |
| `~> 6.52.0` | `>= 6.52.0, < 6.53.0` | коли треба зовсім жорстко |

Правило: **`~>` майже завжди правильна відповідь.**

### А де ж пін точних версій?

Після `terraform init` зʼявиться файл **`.terraform.lock.hcl`** з точними
версіями і їхніми хешами:

```
provider "registry.terraform.io/hashicorp/aws" {
  version     = "6.57.1"
  constraints = "~> 6.52"
  hashes = [ "h1:...", ... ]
}
```

**Цей файл треба комітити в git** (він єдиний із terraform-артефактів, що
комітиться). Саме він гарантує, що у вас і в колеги буде байт-у-байт той самий
провайдер. Оновити його свідомо: `terraform init -upgrade`.

---

## 5. `variables.tf` — построково

Анатомія однієї змінної:

```hcl
variable "region" {
  description = "AWS-регіон, у якому створюємо кластер"
  type        = string
  default     = "eu-central-1"
}
```

| Поле | Навіщо |
|---|---|
| `description` | документація; видно в `terraform console` і в автогенерованих доках |
| `type` | `string`, `number`, `bool`, `list(...)`, `map(...)`, `object({...})`. Передасте не той тип — Terraform впаде ще на `plan`, а не посеред `apply` |
| `default` | значення за замовчуванням. **Якщо його немає — Terraform інтерактивно питатиме значення при кожному запуску** |

### Усі змінні проєкту

Їх рівно вісім — стільки ж, скільки блоків `variable` у файлі:

| Змінна | Тип | Default | Про що подумати |
|---|---|---|---|
| `region` | string | `eu-central-1` | Франкфурт — найближчий до України. `eu-north-1` (Стокгольм) дешевший |
| `cluster_name` | string | `mlops-demo` | ⚠️ якщо група працює в **одному** акаунті — кожен ставить своє унікальне імʼя |
| `kubernetes_version` | string | `1.34` | тримайте в межах ±1 від вашого `kubectl` |
| `instance_type` | string | `t3.medium` | `t3.small` дешевший, але вміщає лише ~11 подів — на демо зі скейлінгом замало |
| `node_desired_size` | number | `3` | не 2: повний стек курсу займає **рівно 34 з 34** слотів на двох нодах. Для самих Тем 5–6 (лише nginx) вистачить `-var node_desired_size=2` |
| `node_ami_release_version` | string | `1.34.9-20260801` | заморожений AMI Amazon Linux 2023. Перша частина мусить збігатися з `kubernetes_version` |
| `node_min_size` | number | `1` | нижче autoscaler не опустить |
| `node_max_size` | number | `3` | **захист від рахунку на $1000**. Має `validation`: `max >= desired`, інакше помилка ще на `plan` |

**`node_desired_size` діє лише при СТВОРЕННІ node group.** Модуль ставить на цей
ресурс `ignore_changes = [scaling_config[0].desired_size]`, тож живому кластеру
ноду додають не через Terraform:

```bash
aws eks update-nodegroup-config --cluster-name mlops-demo --region eu-central-1 \
  --nodegroup-name $(aws eks list-nodegroups --cluster-name mlops-demo \
     --region eu-central-1 --query nodegroups[0] --output text) \
  --scaling-config minSize=1,maxSize=3,desiredSize=3
```

**`node_ami_release_version` — навіщо пін.** Дефолт модуля
`use_latest_ami_release_version = true` означає, що кожен `plan` читає з SSM
найсвіжіший AL2023 і показує зміну `release_version`. AWS випускає збірку
приблизно щотижня, тож `plan` «брудний» постійно, а `apply` на таку зміну — це
rolling replacement **усіх** нод (~10 хв, усі поди переїжджають). Ми ставимо
`use_latest_ami_release_version = false` і фіксуємо версію: у студента, який
робив `apply` у понеділок, і в того, хто у пʼятницю, буде однакове ядро й
containerd. У проді роблять навпаки — оновлюють свідомо, у вікно обслуговування,
бо в новому AMI закриваються CVE.

> Помилка при неузгодженні: `Requested release version 1.33.x is not valid for
> kubernetes version 1.34`. Міняєте `kubernetes_version` — міняйте й цю змінну.

### Три способи перевизначити змінну

Від найнижчого пріоритету до найвищого:

```bash
# 1) Файл terraform.tfvars (не комітиться — див. .gitignore)
echo 'cluster_name = "eks-ivan"' > terraform.tfvars

# 2) Змінна оточення з префіксом TF_VAR_
export TF_VAR_cluster_name="eks-ivan"

# 3) Прапорець у команді — виграє в усіх
terraform apply -var="cluster_name=eks-ivan"
```

> **Порада для пари:** якщо всі в одному акаунті, кожен виконує
> `export TF_VAR_cluster_name="eks-$(whoami)"` — і конфліктів імен не буде.

---

## 6. `main.tf` — построково

Найдовший розділ. Пʼять блоків.

### 6.1. `provider` — до якої хмари підключаємось

```hcl
provider "aws" {
  region = var.region
}
```

| Рядок | Пояснення |
|---|---|
| `provider "aws"` | конфігурація плагіна, який ми оголосили у `versions.tf` |
| `region = var.region` | звертання до змінної через `var.<імʼя>`. Хардкодити регіон — погано: конфіг перестане бути переносимим |

**Чого тут свідомо НЕМАЄ:** `access_key` і `secret_key`. Ніколи не пишіть ключі
в `.tf` — вони одразу потраплять у git. Провайдер сам знайде їх у ланцюжку:
змінні оточення `AWS_ACCESS_KEY_ID` → `~/.aws/credentials` → IAM-роль інстанса.

### 6.2. `data` — запитати те, що ми не створюємо

```hcl
data "aws_availability_zones" "available" {
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}
```

**`resource` vs `data` — ключова різниця:**

| | `resource` | `data` |
|---|---|---|
| Що робить | **створює** ресурс | тільки **читає** існуюче |
| Змінює AWS? | так | ні |
| Що з ним робить `destroy` | видаляє | нічого |

Тут ми питаємо в AWS: «які зони доступні в цьому регіоні?». Хардкодити
`["eu-central-1a", "eu-central-1b"]` не можна — конфіг зламається при зміні регіону.

Фільтр `opt-in-not-required` відсіює **Local Zones** і **Wavelength Zones**: у них
немає EKS, і їх треба окремо активувати в акаунті. Без фільтра ви ризикуєте
отримати зону, у якій кластер просто не створиться.

### 6.3. `locals` — обчислені значення

```hcl
locals {
  vpc_cidr = "10.0.0.0/16"
  azs      = slice(data.aws_availability_zones.available.names, 0, 2)
  tags = {
    Project   = var.cluster_name
    ManagedBy = "Terraform"
    Lesson    = "MLOps-Topic-5-Kubernetes"
  }
}
```

| Рядок | Пояснення |
|---|---|
| `local` vs `variable` | `variable` можна перевизначити ззовні (`-var`, `TF_VAR_`), `local` — **ні**. Це просто «обчислити один раз, використати багато» |
| `vpc_cidr = "10.0.0.0/16"` | адресний простір мережі: `10.0.0.0`–`10.0.255.255`, **65 536** адрес |
| `slice(список, 0, 2)` | взяти елементи з індексами `[0, 1]` — тобто **перші дві** зони. Верхня межа НЕ включається |
| чому 2 зони | EKS вимагає **мінімум 2**. У проді беруть 3 |
| `tags` | щоб у консолі AWS відразу було видно, що це ваше і що створено Terraform, а не руками |

> Є ще блок `default_tags {}` у провайдері — він чіпляє теги на **кожен**
> ресурс автоматично. Ми ним не користуємось: явна передача `tags` у модулі
> наочніша для навчання.

### 6.4. `module "vpc"` — мережа

Це саме те, що на слайді 30 назване *«VPC — із публічними/приватними сабнетами,
Internet/NAT Gateway»*.

```hcl
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = "${var.cluster_name}-vpc"
  cidr = local.vpc_cidr
  azs  = local.azs

  private_subnets = [for k, v in local.azs : cidrsubnet(local.vpc_cidr, 4, k)]
  public_subnets  = [for k, v in local.azs : cidrsubnet(local.vpc_cidr, 8, k + 48)]

  enable_nat_gateway = true
  single_nat_gateway = true

  public_subnet_tags  = { "kubernetes.io/role/elb"          = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }

  tags = local.tags
}
```

**Що таке модуль.** Це чужа тека з `.tf`-файлами, яку Terraform завантажить під
час `init`. Замість того щоб писати ~25 ресурсів вручну (VPC, сабнети, route
tables, IGW, NAT, EIP, асоціації...), ми беремо перевірений спільнотою модуль.

| Рядок | Пояснення |
|---|---|
| `source = "terraform-aws-modules/vpc/aws"` | скорочення для Terraform Registry. Може бути й `git::https://...` або локальний шлях `./modules/vpc` |
| `version = "~> 6.0"` | **пін обовʼязковий.** Без нього завтра приїде 7.0 і зламає конфіг |
| `name` | `"${var.cluster_name}-vpc"` — інтерполяція рядка, дасть `mlops-demo-vpc` |
| `azs = local.azs` | у яких зонах створювати підмережі |

#### Математика підмереж

```hcl
cidrsubnet(prefix, newbits, netnum)
```

Функція «відріж від мережі `prefix` підмережу, додавши `newbits` біт до маски,
і візьми шматок номер `netnum`».

| Виклик | Результат | Адрес |
|---|---|---|
| `cidrsubnet("10.0.0.0/16", 4, 0)` | `10.0.0.0/20` | 4 094 |
| `cidrsubnet("10.0.0.0/16", 4, 1)` | `10.0.16.0/20` | 4 094 |
| `cidrsubnet("10.0.0.0/16", 8, 48)` | `10.0.48.0/24` | 251 |
| `cidrsubnet("10.0.0.0/16", 8, 49)` | `10.0.49.0/24` | 251 |

`16 + 4 = /20` для приватних, `16 + 8 = /24` для публічних. Зсув `+48` для
публічних — просто щоб діапазони гарантовано не перетнулись.

**Чому приватні підмережі такі великі (4 094 адреси)?** Через **VPC CNI**: у EKS
кожен под отримує **справжню IP-адресу з підмережі VPC**, а не внутрішню
overlay-адресу як у більшості інших CNI. Маленька підмережа = швидко скінчаться
IP = поди зависнуть у `Pending`.

#### Публічні vs приватні

| | Публічні (`10.0.48.0/24`) | Приватні (`10.0.0.0/20`) |
|---|---|---|
| Публічна IP | є | немає |
| Доступ **з** інтернету | так | **ні** |
| Доступ **в** інтернет | напряму через IGW | через NAT Gateway |
| Що там живе | NAT Gateway, Load Balancer | **worker-ноди й поди** |

Ноди в приватних підмережах — це безпека: до них неможливо достукатись ззовні,
але вони можуть завантажити Docker-образ.

| Рядок | Пояснення |
|---|---|
| `enable_nat_gateway = true` | без NAT ноди не зможуть скачати образи і **навіть не приєднаються до кластера** |
| `single_nat_gateway = true` | один NAT на всю VPC замість одного на зону. Економія ~$35/міс. Ціна: якщо впаде зона з NAT — усі ноди втратять вихід в інтернет. Для навчання ок, для проду — ні |

#### Теги підмереж — найпідступніший рядок у файлі

```hcl
public_subnet_tags  = { "kubernetes.io/role/elb"          = 1 }
private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
```

Коли ви створите `Service` типу `LoadBalancer`, контролер AWS шукатиме, у яку
підмережу поставити балансувальник — **і шукає він саме за цими тегами**.

Забули тег → `kubectl get svc` вічно показує `EXTERNAL-IP: <pending>`, і в логах
жодної зрозумілої помилки. Класична багатогодинна відладка.

> 💡 **Відповідь на питання зі слайда 37** («застосунок має бути доступний лише
> для внутрішніх сервісів у VPC»): тег `internal-elb` + анотація на Service
> `service.beta.kubernetes.io/aws-load-balancer-internal: "true"` — і
> балансувальник отримає лише приватну IP, недоступну з інтернету.

### 6.5. `module "eks"` — власне кластер

Офіційний модуль зі слайда 32. Створює ~40 ресурсів.

```hcl
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  endpoint_public_access                   = true
  enable_cluster_creator_admin_permissions = true

  addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni    = { before_compute = true }
  }

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  eks_managed_node_groups = {
    default = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = [var.instance_type]
      capacity_type  = "ON_DEMAND"
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size
    }
  }

  tags = local.tags
}
```

> ### ⚠️ Модуль v21 ≠ v20
> У версії 21 **перейменували майже всі змінні**. Якщо ви знайдете туторіал в
> інтернеті — він майже напевно на v20 і **не запуститься** копіпастом:
>
> | v20 (старе) | v21 (наше) |
> |---|---|
> | `cluster_name` | `name` |
> | `cluster_version` | `kubernetes_version` |
> | `cluster_endpoint_public_access` | `endpoint_public_access` |
> | `cluster_addons` | `addons` |
>
> Симптом: `Error: Unsupported argument ... An argument named "cluster_name" is not expected here.`

| Рядок | Пояснення |
|---|---|
| `name` | імʼя кластера. Його ж вкажете в `kubectl` і побачите в консолі |
| `kubernetes_version` | версія **control plane** — тієї частини, якою керує AWS (слайд 28) |
| `endpoint_public_access = true` | API Server отримує публічну адресу, і `kubectl` працює з вашого ноутбука. У проді — `false` + доступ через VPN/bastion |

#### `enable_cluster_creator_admin_permissions` — не пропустіть цей рядок

```hcl
enable_cluster_creator_admin_permissions = true
```

Створює **Access Entry** для того IAM-користувача, який виконав `apply`.

**Без цього рядка** кластер створиться успішно, `terraform apply` завершиться
зеленим, а `kubectl get nodes` відповість:

```
error: You must be logged in to the server (Unauthorized)
```

Тобто ви власник кластера в AWS, але ніхто в самому Kubernetes.

> На слайді 28 згадано `aws-auth` ConfigMap — це **старий** механізм. Сучасний
> підхід — **Access Entries** (API-рівень, керується Terraform, а не редагуванням
> YAML у кластері). Ми користуємось новим.

Обмежити доступ до API конкретною IP:

```hcl
endpoint_public_access_cidrs = ["203.0.113.42/32"]
```

#### `addons` — системні компоненти

Це ті самі компоненти Worker Node зі слайдів 19–22, але в керованому AWS вигляді:

| Addon | Що робить | Слайд |
|---|---|---|
| `coredns` | DNS усередині кластера: `nginx-demo` → IP сервісу | — |
| `kube-proxy` | мережеві правила і балансування між подами | 22 |
| `vpc-cni` | видає кожному поду **реальну IP з підмережі VPC** | 21 |
| `eks-pod-identity-agent` | віддає поду тимчасові AWS-креденшели його IAM-ролі | — |
| `aws-ebs-csi-driver` | **створює і підключає EBS-диски** під `PersistentVolumeClaim` | — |

```hcl
vpc-cni = { before_compute = true }
```

`before_compute = true` означає «постав цей addon **ще до** створення нод».
Інакше перші ноди піднімуться зі старою версією CNI, і їх доведеться
перестворювати. Класична причина «чомусь поди не отримують IP».

> **Ціна в подах.** Addon — це поди на ваших нодах: `ebs-csi-controller`
> (2 репліки) + `ebs-csi-node` (DaemonSet, 1 на ноду) + `eks-pod-identity-agent`
> (DaemonSet) = **+6 подів на 2-нодовому кластері**. На `t3.medium` ліміт ~17
> подів на ноду, тому це не дрібниця — див. коментар до `node_desired_size`
> у [`variables.tf`](../terraform/cluster/variables.tf).

#### Сховище: EBS CSI driver і чому дефолтний `gp2` мертвий

**Що було до цього розділу.** Кластер створювався без жодного CSI-драйвера,
і будь-який `PersistentVolumeClaim` залишався в `Pending` **назавжди**. Тому
Теми 5–8 їхали без персистентності: `persistence.enabled=false`, `emptyDir`,
дані живуть рівно стільки, скільки под. Це працювало, поки ми не дійшли до
MLflow, PostgreSQL і MinIO — вчити «зберігайте моделі, щоб не втратити роботу»
на `emptyDir` неможливо: перший рестарт пода знищує всі експерименти.

**Чому саме воно не працювало** — цей матеріал цінніший за саме виправлення.
EKS створює кластеру один StorageClass `gp2` з таким провізіонером:

```bash
kubectl get sc
# NAME   PROVISIONER             ...
# gp2    kubernetes.io/aws-ebs   ← in-tree плагін
```

`kubernetes.io/aws-ebs` — це **in-tree** плагін: код драйвера жив усередині
самого Kubernetes (у `kube-controller-manager`). Його **вилучено у версії 1.31**
разом із логікою CSI-міграції. Наш кластер — **1.34**, тобто цей рядок не
обробляє **ніхто**: у контролері коду вже немає, а CSI-драйвер слухає лише своє
власне імʼя.

Три причини, чому `gp2` **неможливо** полагодити — кожної досить:

| # | Причина |
|---|---|
| 1 | Поле `provisioner` **іммутабельне**. Патч `gp2` → `ebs.csi.aws.com` API server відкине: `StorageClass.provisioner: Invalid value: field is immutable` |
| 2 | Сайдкар `csi-provisioner` підписаний **лише** на PVC, чий клас має `provisioner == ebs.csi.aws.com`. PVC на `kubernetes.io/aws-ebs` він не бачить — це не помилка доступу, це відсутність підписки |
| 3 | Механізму CSI-міграції, який колись транслював in-tree → CSI, на 1.34 вже немає |

**Діагностичний відбиток, який варто показати студентам.** `kubectl describe pvc`
на класі `gp2`: PVC у `Pending`, а в `Events` **немає ані** `ProvisioningFailed`,
**ані** згадки будь-якого провізіонера — максимум `waiting for a volume to be
created`. **Тиша в подіях = ніхто не взявся за роботу.** Порівняйте з PVC на
`gp3`, де відразу зʼявляються `Provisioning` / `ProvisioningSucceeded` від
`ebs.csi.aws.com_...`.

**Що ми ставимо замість.** Два addon-и вище + IAM-роль у
[`terraform/cluster/ebs-csi-iam.tf`](../terraform/cluster/ebs-csi-iam.tf) + новий StorageClass.
Драйвер — це под, а поду потрібне право на `ec2:CreateVolume`, тож роль
обовʼязкова: без неї addon буде `ACTIVE`, а кожен PVC впаде з `AccessDenied`.
Права видаємо через **EKS Pod Identity** (сучасний механізм; trust policy на
`pods.eks.amazonaws.com`), а не через ключі в секреті. Варіант з **IRSA** —
закоментований у тому ж файлі: він економить 2 поди агента, і це реальний
аргумент на маленькому кластері.

**StorageClass — це Kubernetes, а не AWS**, тому він у
[`deploy/0-storage/storageclass-gp3.yaml`](../deploy/0-storage/storageclass-gp3.yaml),
а не в Terraform. Причина в межі відповідальності: `kubernetes_storage_class`
затягнув би в проєкт provider `kubernetes` з автентифікацією через
`aws eks get-token`, дав би класичне `Provider configuration unknown` на першому
`plan` (провайдер конфігурується з `module.eks`, якого ще нема) і поклав би
кластерні обʼєкти в tfstate, який `destroy` намагатиметься чистити вже після
знесеного API server. **Terraform створює AWS. Kubernetes-обʼєкти створює
`kubectl`.**

Порядок застосування, і він **важливий**:

```bash
# 1. Драйвер мусить існувати ДО першого PVC
cd terraform/cluster && terraform apply && cd ../..

# 2. Зняти дефолт зі старого класу. Мінус у кінці = ВИДАЛИТИ анотацію.
#    Два дефолтних класи = недетермінований вибір: PVC може приліпитись
#    до мертвого gp2, і це виглядатиме як «драйвер не працює».
kubectl annotate storageclass gp2 storageclass.kubernetes.io/is-default-class-

# 3. Новий клас (імʼя нове, бо gp2 полагодити неможливо — див. таблицю вище)
kubectl apply -f deploy/0-storage/storageclass-gp3.yaml
kubectl get sc            # рівно ОДИН рядок містить (default)

# 4. Перевірка драйвера. Дивитись КОЛОНКУ READY, а не STATUS:
#    ebs-csi-controller — под із ~6 сайдкарами, буває Running при READY 5/6
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver
kubectl get csidrivers    # має зʼявитись ebs.csi.aws.com

# 5. Димовий тест: PVC мусить перейти Pending -> Bound за ~15 секунд
kubectl apply -f deploy/0-storage/smoke-test.yaml
kubectl get pvc gp3-smoke -w
kubectl logs pod/gp3-smoke                            # ok
kubectl delete -f deploy/0-storage/smoke-test.yaml    # 🔴 не забути!
```

> **`make cluster-up` робить кроки 1 і 3 за вас** — `terraform apply`, потім
> `aws eks update-kubeconfig`, потім `kubectl apply -f deploy/0-storage/storageclass-gp3.yaml`.
> Крок 2 (зняти дефолт із `gp2`) він **не** робить: анотацію ставить сам EKS при
> створенні кластера, і на свіжому кластері у вас буде два дефолтних класи.
> Перевірте `kubectl get sc` — `(default)` має бути рівно в одному рядку.

`gp3` замість `gp2` не з примхи: він **на 20% дешевший** ($0.0952 проти $0.119
за GiB-міс у Франкфурті) і вже включає 3000 IOPS та 125 MB/s. Реалістичні
16 GiB (MinIO + PostgreSQL) — це **$1.52/міс, ~5 центів на добу**. Для порівняння:
сам кластер із NAT Gateway коштує ~$5.95/добу. Сховище — найдешевша частина.

> **🔴 EBS зонний, і це вбиває тихо.** Диск живе в **одній** Availability Zone і
> прибитий до неї назавжди. Под із таким PVC ніколи не запланується на ноду в
> іншій зоні: `FailedScheduling ... node(s) had volume node affinity conflict`.
> Саме тому у StorageClass стоїть `volumeBindingMode: WaitForFirstConsumer` —
> спершу scheduler обирає ноду, і лише потім диск створюється **в її зоні**.
> Це рятує при створенні тому, але **не** при подальшому переїзді пода: якщо
> нода з єдиним диском померла, под чекатиме її повернення вічно.

#### Підключення до мережі

```hcl
vpc_id     = module.vpc.vpc_id
subnet_ids = module.vpc.private_subnets
```

Це не просто передача значень — **саме ці два рядки створюють залежність**
у графі: «спочатку VPC, потім EKS». Terraform виводить порядок сам, вам не
треба нічого впорядковувати вручну.

`private_subnets` — бо, як каже слайд 19, *«Pod-и ніколи не живуть на Control
Plane — тільки на Worker Nodes»*, а ноди ми ховаємо в приватну мережу.

#### `eks_managed_node_groups` — worker-ноди

Слайд 30: *«EKS Node Group або Fargate Profile — для запуску подів»*.

**Managed Node Group** означає, що AWS сам створює Auto Scaling Group, ставить
правильний AMI, реєструє ноду в кластері й уміє робити rolling-update при
апгрейді Kubernetes.

| Рядок | Пояснення |
|---|---|
| `default = { }` | довільне імʼя групи. Груп може бути кілька — наприклад, окрема з GPU для ML-інференсу |
| `ami_type = "AL2023_x86_64_STANDARD"` | Amazon Linux 2023 — дефолт для EKS з версії 1.30. Для GPU був би `AL2023_x86_64_NVIDIA` |
| `instance_types = [...]` | список, а не один рядок: кілька типів = більше шансів отримати SPOT-потужності |
| `capacity_type = "ON_DEMAND"` | стабільно. `SPOT` дешевший на ~70%, але AWS може забрати інстанс за 2 хв попередження — не те, що потрібно посеред пари |
| `min_size` / `max_size` / `desired_size` | межі Auto Scaling Group (слайд 25). `desired` — скільки зараз, `min`/`max` — у яких межах може змінюватись |

---

## 7. `outputs.tf` — построково

```hcl
output "cluster_endpoint" {
  description = "HTTPS-адреса Kubernetes API Server"
  value       = module.eks.cluster_endpoint
}
```

**Output** — це спосіб дістати значення зі стейту назовні:

- показати людині наприкінці `apply`;
- прочитати скриптом чи з CI: `terraform output -raw configure_kubectl`;
- передати в інший Terraform-проєкт через `terraform_remote_state`.

| Output | Що дає |
|---|---|
| `cluster_name` | імʼя кластера |
| `cluster_endpoint` | адреса API Server — саме туди ходить `kubectl` (слайд 15) |
| `cluster_version` | яку версію реально підняв AWS |
| `node_group_role` | IAM-роль нод; через неї ноди мають право приєднатись до кластера |
| `configure_kubectl` | **готова до копіювання команда** |

Останній — найкорисніший:

```hcl
output "configure_kubectl" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
```

Він краще за інструкцію в README, бо регіон та імʼя підставлені **вашими**
значеннями — студент не переплутає їх із тими, що в методичці.

<details>
<summary><b>Якщо в output потрапляє секрет</b></summary>

```hcl
output "cluster_ca" {
  value     = module.eks.cluster_certificate_authority_data
  sensitive = true    # Terraform надрукує (sensitive value) замість значення
}
```

Але памʼятайте: **у файлі стейту воно все одно лежить у відкритому вигляді**.
`sensitive` ховає значення лише від друку в термінал.
</details>

---

## 8. Запуск: init → plan → apply

> ### Коротка дорога — і чому ми йдемо довгою
> У корені репозиторію є ціль, яка робить увесь цей розділ однією командою:
>
> ```bash
> make cluster-up
> ```
>
> Усередині неї рівно чотири команди (подивіться самі: `Makefile`, ціль
> `cluster-up`): `terraform init -input=false`, `terraform apply`,
> `aws eks update-kubeconfig --name mlops-demo`, `kubectl apply -f
> deploy/0-storage/storageclass-gp3.yaml`. Тобто розділи 8 і 9 цього гайду
> плюс StorageClass із 6.5.
>
> На занятті пройдіть **вручну**: `make cluster-up` не показує ані `plan`, ані
> того, що саме Terraform створює, а вміння прочитати `plan` до `apply` — це і є
> зміст Теми 5. Далі, коли кластер треба буде просто підняти, користуйтесь `make`.
>
> ⚠️ Імʼя `mlops-demo` у `make cluster-up` **зашите**. Якщо ви змінили
> `cluster_name` (група в одному акаунті — див. розділ 5), `terraform apply`
> відпрацює, а наступний рядок впаде з `ResourceNotFoundException: No cluster
> found for name: mlops-demo`. Тоді `update-kubeconfig` робіть руками — розділ 9.

```bash
cd terraform/cluster
export AWS_PROFILE=ваш-профіль    # якщо у вас не default-профіль
```

### 8.1. `terraform init` — підготовка

```bash
terraform init
```

Що відбувається:
1. читає `versions.tf` і завантажує провайдери у теку `.terraform/`;
2. завантажує модулі (`vpc`, `eks`) з Registry;
3. створює `.terraform.lock.hcl` із точними версіями і хешами.

Очікуваний результат:

```
- Installing hashicorp/aws v6.57.1...
- Installing hashicorp/tls v4.3.0...
- Installing hashicorp/time v0.14.0...
- Installing hashicorp/cloudinit v2.4.0...
- Installing hashicorp/null v3.3.0...

Terraform has been successfully initialized!
```

> Ми оголосили лише `aws`, а поставилось пʼять провайдерів. Решту потягнув
> **модуль EKS** — модулі мають власні залежності.

Перевірити, які версії модулів приїхали:

```bash
terraform providers
```

### 8.2. `fmt` і `validate` — дешеві перевірки

```bash
terraform fmt        # автоформатування (як gofmt / black). Правки — на місці
terraform fmt -check # тільки перевірити, нічого не міняти. Для CI
terraform validate   # синтаксис + типи + чи існують такі аргументи в модулях
```

`validate` **не ходить в AWS** — працює без креденшелів і за секунду. Запускайте
його після кожної правки, це найдешевший спосіб зловити помилку.

```
Success! The configuration is valid.
```

### 8.3. `terraform plan` — сухий прогін

```bash
terraform plan
```

**`plan` нічого не змінює.** Він порівнює три речі:

```
   що описано в .tf   ⟷   що записано у .tfstate   ⟷   що реально є в AWS
```

і показує різницю. Читати вивід так:

| Символ | Значення |
|---|---|
| `+` | створити |
| `-` | **видалити** |
| `~` | змінити на місці |
| `-/+` | **перестворити** (видалити і створити заново — буде даунтайм!) |

Останній рядок — найважливіший:

```
Plan: 62 to add, 0 to change, 0 to destroy.
```

**62 ресурси** з ~60 рядків нашого коду. Ось за що ми любимо модулі.

> ### 🔴 Дисципліна
> **Завжди читайте `plan` перед `apply`.** Особливо шукайте `-` та `-/+` —
> це видалення. У проді саме тут ловлять «ой, я щойно перестворив продакшн-базу».

Зберегти план і застосувати рівно його:

```bash
terraform plan -out=tfplan     # у бінарний файл (містить секрети — у .gitignore!)
terraform apply tfplan         # застосувати саме цей план, без повторних питань
```

### 8.4. `terraform apply` — створюємо

```bash
terraform apply
```

Terraform ще раз покаже план і спитає підтвердження — треба ввести **`yes`**
повністю (`y` не спрацює).

**⏱️ Це найдовший крок: ~15 хвилин.** Тайминг:

| Етап | Час |
|---|---|
| VPC, підмережі, IGW | ~30 с |
| NAT Gateway | ~2 хв |
| IAM-ролі та політики | ~20 с |
| **EKS control plane** | **~9–10 хв** ← найдовше |
| Addons (`vpc-cni`) | ~1 хв |
| Managed Node Group | ~3 хв |

> **Не закривайте термінал.** Якщо `apply` перервати посеред роботи, ресурси
> в AWS залишаться, а стейт буде неповним. Полагодити можна, але це нудно.
> Гарний момент, щоб повернутись до теорії й розібрати слайди 12–22.

Успішне завершення:

```
Apply complete! Resources: 62 added, 0 changed, 0 destroyed.

Outputs:

cluster_endpoint  = "https://XXXX.gr7.eu-central-1.eks.amazonaws.com"
cluster_name      = "mlops-demo"
cluster_version   = "1.34"
configure_kubectl = "aws eks update-kubeconfig --region eu-central-1 --name mlops-demo"
```

---

## 9. Підключаємо kubectl

Кластер створено, але `kubectl` про нього ще не знає.

```bash
aws eks update-kubeconfig --region eu-central-1 --name mlops-demo
```

Або одразу з output — без шансу помилитись:

```bash
$(terraform output -raw configure_kubectl)
```

### Що саме зробила ця команда

Вона дописала в `~/.kube/config` три речі: **cluster** (адреса API + CA-сертифікат),
**user** (як отримувати токен) і **context** (звʼязка cluster + user).

```bash
kubectl config current-context
# arn:aws:eks:eu-central-1:123456789012:cluster/mlops-demo

kubectl config get-contexts     # усі кластери, які знає kubectl
```

Цікаве в секції `user`: там **немає пароля чи токена**. Є команда:

```yaml
exec:
  command: aws
  args: ["--region", "eu-central-1", "eks", "get-token", "--cluster-name", "mlops-demo"]
```

`kubectl` **щоразу** викликає `aws` і отримує свіжий тимчасовий токен. Тому:
зникли AWS-креденшели → `kubectl` перестав працювати. Це фіча, а не баг.

### Перевірка — момент істини

```bash
kubectl get nodes -o wide
```

```
NAME                          STATUS   ROLES    AGE   VERSION
ip-10-0-1-123.ec2.internal    Ready    <none>   2m    v1.34.x
ip-10-0-8-201.ec2.internal    Ready    <none>   2m    v1.34.x
ip-10-0-17-45.ec2.internal    Ready    <none>   2m    v1.34.x
```

Три ноди в статусі `Ready` — кластер живий. 🎉 (Дві, якщо ви робили
`apply -var node_desired_size=2`.)

> Якщо `STATUS` = `NotReady` — зачекайте 1–2 хвилини, ноди ще піднімають CNI.

### Подивимось на компоненти зі слайдів 19–22

```bash
kubectl get pods -n kube-system
```

```
NAME                                  READY   STATUS
aws-node-xxxxx                        2/2     Running   ← VPC CNI, роздає IP подам
aws-node-yyyyy                        2/2     Running
coredns-xxxxxxxxxx-aaaaa              1/1     Running   ← DNS кластера
coredns-xxxxxxxxxx-bbbbb              1/1     Running
kube-proxy-xxxxx                      1/1     Running   ← слайд 22
kube-proxy-yyyyy                      1/1     Running
ebs-csi-controller-xxxxxxxxx-aaaaa    6/6     Running   ← створює EBS-диски
ebs-csi-controller-xxxxxxxxx-bbbbb    6/6     Running
ebs-csi-node-xxxxx                    3/3     Running   ← монтує їх у поди
ebs-csi-node-yyyyy                    3/3     Running
eks-pod-identity-agent-xxxxx          1/1     Running   ← віддає поду IAM-креденшели
eks-pod-identity-agent-yyyyy          1/1     Running
```

Зверніть увагу: `aws-node`, `kube-proxy`, `ebs-csi-node` і
`eks-pod-identity-agent` — рівно по **одному на ноду**. Це DaemonSet: «запусти
цей под на кожній ноді».

І одразу тренуйте головну звичку: у `ebs-csi-controller` дивіться на **`READY
6/6`**, а не на `Running`. Под із шістьома сайдкарами буває `Running` при
`READY 5/6` — один сайдкар тихо помер по OOM, а `STATUS` про це не скаже.

> **А де Control Plane?** Його подів ви не побачите — `api-server`, `scheduler`,
> `etcd` крутяться в акаунті AWS, а не у вашому. Це і є суть керованого сервісу
> (слайд 28).

```bash
kubectl cluster-info      # адреса API Server
kubectl get namespaces    # логічні «теки» кластера
```

---

## 10. Деплой застосунку

Нам потрібні два обʼєкти: **Deployment** (тримає 2 поди) і **Service** типу
`LoadBalancer` (публічна точка входу). Файлу для них у репозиторії немає навмисно:
у Темі 6 ті самі ресурси будуть у `deploy/1-kubectl/` — але там `Service` має тип
`ClusterIP`, бо чотири балансувальники по $0.03/год за одну пару нікому не
потрібні. Тут же нам потрібен саме `LoadBalancer`: він і показує роботу тегів
підмереж із розділу 6.4, і створює ту саму пастку, заради якої існує розділ 13.

Тому — просто в термінал:

```bash
cd ../..                         # у корінь репозиторію

kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nginx-demo
spec:
  replicas: 2
  selector:
    matchLabels: { app: nginx-demo }
  template:
    metadata:
      labels: { app: nginx-demo }
    spec:
      containers:
        - name: nginx
          # public.ecr.aws, а не Docker Hub: у Hub анонімний ліміт 100 pull/6 год
          # на IP, а всі ноди виходять в інтернет через ОДИН NAT Gateway —
          # тобто на всю групу одна квота. Симптом: ErrImagePull "toomanyrequests".
          image: public.ecr.aws/nginx/nginx:1.29-alpine
          ports: [{ containerPort: 80 }]
          resources:
            requests: { cpu: 50m, memory: 32Mi }
            limits:   { cpu: 200m, memory: 128Mi }
---
apiVersion: v1
kind: Service
metadata:
  name: nginx-demo
spec:
  type: LoadBalancer      # ← саме через це AWS створить Classic Load Balancer
  selector: { app: nginx-demo }
  ports:
    - port: 80
      targetPort: 80
EOF
```

```
deployment.apps/nginx-demo created
service/nginx-demo created
```

### Дивимось, що вийшло

```bash
kubectl get deployment,pods,svc
```

```
NAME                         READY   UP-TO-DATE   AVAILABLE
deployment.apps/nginx-demo   2/2     2            2

NAME                              READY   STATUS    RESTARTS
pod/nginx-demo-7d9f8c6b4d-abcde   1/1     Running   0
pod/nginx-demo-7d9f8c6b4d-fghij   1/1     Running   0

NAME                 TYPE           EXTERNAL-IP                        PORT(S)
service/nginx-demo   LoadBalancer   a1b2c3...eu-central-1.elb.amaz...  80:31234/TCP
```

**`EXTERNAL-IP` зʼявляється не одразу** — AWS створює балансувальник 2–4 хвилини.
Поки він `<pending>`, чекайте:

```bash
kubectl get svc nginx-demo -w      # -w = watch, оновлюється наживо. Ctrl+C щоб вийти
```

### Відкриваємо в браузері

```bash
export LB=$(kubectl get svc nginx-demo -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
echo "http://$LB"
curl -s "http://$LB" | head -5
```

```html
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
```

**Працює.** Ваш застосунок доступний з інтернету через AWS Load Balancer. 🎉

> DNS-імʼя балансувальника може розʼїжджатись 1–2 хвилини — якщо `curl` каже
> `Could not resolve host`, зачекайте і повторіть.

### 🔴 Запамʼятайте про цей Load Balancer

Його створив **Kubernetes**, а не Terraform. У файлі `terraform.tfstate` його
**немає**. Отже `terraform destroy` про нього не знає і **не видалить** —
але видалити VPC теж не зможе, бо балансувальник її тримає. Розділ 13.

---

## 11. kubectl і fault-tolerance наживо

### 11.1. Шпаргалка (слайди 33–35)

Формат команди: `kubectl <дія> <ресурс> <назва>`

**Дивитись:**
```bash
kubectl get pods                     # у поточному namespace
kubectl get pods -A                  # у всіх namespace
kubectl get pods -o wide             # + на якій ноді й з якою IP
kubectl get all                      # усі основні ресурси
kubectl describe pod <name>          # ПОВНИЙ опис + події внизу ← головне при відладці
kubectl get events --sort-by=.lastTimestamp
```

**Логи й відладка:**
```bash
kubectl logs <pod>                   # логи
kubectl logs <pod> -f                # стежити наживо (як tail -f)
kubectl logs <pod> --previous        # логи ПОПЕРЕДНЬОГО контейнера — якщо под падає в CrashLoop
kubectl exec -it <pod> -- sh         # зайти всередину контейнера
kubectl port-forward svc/nginx-demo 8080:80   # прокинути порт на localhost:8080
```

**Змінювати:**
```bash
kubectl apply -f file.yaml           # створити або оновити (декларативно) ← так правильно
kubectl delete -f file.yaml          # видалити все з файлу
kubectl scale deployment/nginx-demo --replicas=5
kubectl rollout restart deployment/nginx-demo   # перезапустити всі поди
kubectl rollout undo deployment/nginx-demo      # відкотити останнє оновлення
```

**Ресурси й ноди:**
```bash
kubectl get nodes -o wide
kubectl describe node <node>         # скільки ресурсів зайнято
kubectl top nodes                    # потребує metrics-server
```

> **Найважливіша команда — `kubectl describe`.** Секція `Events` унизу пояснює
> 90% проблем: чому под не стартує, чому не влазить на ноду, чому не тягнеться образ.

### 11.2. Самовідновлення — вбиваємо под (слайд 26)

У **першому** терміналі запускаємо спостереження:

```bash
kubectl get pods -w
```

У **другому** — вбиваємо под:

```bash
kubectl delete pod $(kubectl get pods -l app=nginx-demo -o jsonpath='{.items[0].metadata.name}')
```

У першому терміналі побачите приблизно таке:

```
nginx-demo-7d9f8c6b4d-abcde   1/1     Running       0
nginx-demo-7d9f8c6b4d-abcde   1/1     Terminating   0     ← вбили
nginx-demo-7d9f8c6b4d-zzzzz   0/1     Pending       0     ← ReplicaSet ОДРАЗУ створив новий
nginx-demo-7d9f8c6b4d-zzzzz   0/1     ContainerCreating
nginx-demo-7d9f8c6b4d-zzzzz   1/1     Running       0     ← ~10 секунд
```

**Ніхто нічого не робив руками.** `ReplicaSet Controller` (слайд 17) побачив, що
подів стало 1 замість 2, і виправив це за секунди. Це і є «самовідновлення»
зі слайда 26.

Застосунок при цьому **не падав**: Service продовжував слати трафік у другий под.
Перевірте — `curl` працює весь час:

```bash
while true; do curl -s -o /dev/null -w "%{http_code} " "http://$LB"; sleep 1; done
```

### 11.3. Ручне масштабування (слайд 25)

```bash
kubectl scale deployment/nginx-demo --replicas=5
kubectl get pods -o wide
```

Зверніть увагу в колонці `NODE`: Scheduler (слайд 16) **сам** розкидав поди між
двома нодами, зважаючи на вільні ресурси.

Повертаємо:

```bash
kubectl scale deployment/nginx-demo --replicas=2
```

### 11.4. Автоматичне масштабування — HPA (слайд 25)

```bash
kubectl autoscale deployment nginx-demo --cpu=50 --min=2 --max=10
kubectl get hpa
```

> ⚠️ У `kubectl` 1.34 прапорець називається `--cpu`. У старих туторіалах ви
> побачите `--cpu-percent` — його **прибрали**. Симптом: `unknown flag`.

```
NAME         REFERENCE               TARGETS         MINPODS   MAXPODS   REPLICAS
nginx-demo   Deployment/nginx-demo   <unknown>/50%   2         10        2
```

`TARGETS` = `<unknown>` — це нормально й очікувано: **HPA не має звідки взяти
метрики CPU**. Йому потрібен `metrics-server`, якого в EKS немає з коробки:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl get hpa -w      # через ~1 хв замість <unknown> зʼявиться 0%/50%
```

> Це гарна ілюстрація для слайда 24: EKS дає **голий** Kubernetes. Метрики,
> ingress-контролер, логування, cert-manager — усе це ви ставите самі
> (зазвичай Helm-чартами або через ArgoCD).

Прибираємо за собою:

```bash
kubectl delete hpa nginx-demo
```

### 11.5. Що ще подивитись, якщо є час

```bash
# де саме крутиться под і скільки він їсть
kubectl describe pod <name> | grep -A5 "Node:"

# знайти вузьке місце: скільки ресурсів вільно на нодах
kubectl describe node | grep -A8 "Allocated resources"

# симуляція: попросити більше CPU, ніж є в кластері -> под зависне в Pending
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: hungry
spec:
  containers:
    - name: hungry
      image: nginx
      resources:
        requests:
          cpu: "8"          # 8 ядер — на t3.medium (2 vCPU) не влізе ніколи
EOF

kubectl get pod hungry                     # STATUS: Pending — назавжди
kubectl describe pod hungry | tail -8      # Events: "Insufficient cpu"
kubectl delete pod hungry
```

Це наочно показує роботу **Scheduler** (слайд 16): він не запускає под «як
вийде» — він **не знаходить** ноду, яка задовольняє `requests`, і чесно лишає
под у `Pending` замість того, щоб покласти ноду.

---

## 12. Бонус: стейт у S3

### Проблема (слайд 36)

Зараз `terraform.tfstate` лежить у вас на ноутбуці. Це погано з трьох причин:

1. **Втрата = катастрофа.** Стейт — це єдина мапа «мій код ↔ реальні ресурси
   в AWS». Втратили файл — Terraform більше не знає про 62 створених ресурси
   і при наступному `apply` спробує створити їх ще раз.
2. **Командна робота неможлива.** У колеги свій стейт, у вас свій. Два `apply`
   одночасно — і ресурси затруть один одного.
3. **Секрети.** У стейті у **відкритому вигляді** лежать паролі, токени,
   сертифікати. Тому `*.tfstate` і стоїть у `.gitignore`.

Рішення — **remote backend**: стейт в S3, з блокуванням на час `apply`.

### 12.1. Створюємо бакет

Курка і яйце: бакет для стейту не можна створити тим самим Terraform, чий стейт
у ньому лежатиме. Тому — руками, один раз:

```bash
# Імʼя бакета має бути унікальним у СВІТІ. Додайте номер акаунта:
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export BUCKET="mlops-tfstate-$ACCOUNT"

aws s3api create-bucket \
  --bucket "$BUCKET" \
  --region eu-central-1 \
  --create-bucket-configuration LocationConstraint=eu-central-1

# Версіонування — щоб можна було відкотити зіпсований стейт
aws s3api put-bucket-versioning \
  --bucket "$BUCKET" --versioning-configuration Status=Enabled

# Шифрування
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# Заблокувати публічний доступ
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "Бакет: $BUCKET"
```

### 12.2. Вмикаємо backend

У `terraform/cluster/versions.tf` розкоментуйте блок і підставте своє імʼя бакета:

```hcl
terraform {
  backend "s3" {
    bucket       = "mlops-tfstate-123456789012"   # ваше імʼя з кроку вище
    key          = "eks/terraform.tfstate"        # шлях усередині бакета
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true                           # блокування паралельних apply
  }
}
```

| Рядок | Пояснення |
|---|---|
| `key` | шлях у бакеті. Різні проєкти = різні `key` в одному бакеті |
| `encrypt = true` | шифрувати при записі |
| `use_lockfile = true` | ⭐ S3-нативне блокування. Поки хтось робить `apply`, поруч зʼявляється `.tflock`-файл, і другий `apply` отримає помилку замість того, щоб зіпсувати стейт |

> **Актуально:** раніше для блокування була потрібна окрема таблиця DynamoDB
> (`dynamodb_table`). Починаючи з Terraform 1.10 це робить сам S3 через
> `use_lockfile`. Якщо бачите в туторіалі DynamoDB — туторіал застарів.

### 12.3. Мігруємо стейт

```bash
cd terraform/cluster
terraform init -migrate-state
```

Terraform спитає: *"Do you want to copy existing state to the new backend?"* →
**`yes`**.

Перевірка:

```bash
aws s3 ls "s3://$BUCKET/eks/"
terraform state list | head    # має і далі бачити всі 62 ресурси
```

Тепер локальний `terraform.tfstate` можна видалити — джерело істини в S3.

---

## 13. 🔴 Прибирання і контроль витрат

> **Це найважливіший розділ.** Кластер, який забули знести, коштує **~$235/міс**.

### Порядок має значення

```
 1. kubectl delete (+ PVC)  →  2. LB зник, диски available  →  3. terraform destroy
```

> ### 🔴 Якщо ви вже проходили Теми 6–10 на цьому кластері
> Спершу **`make down`** — і лише потім усе нижче. `make down` видаляє
> `argocd/root.yaml`, а його фіналайзер каскадом зносить десять дочірніх
> Application і все, що вони створили: Service, PVC, namespace.
>
> Пропустите цей крок — `terraform destroy` крутитиметься ~20 хвилин і впаде на
> `DependencyViolation: The subnet has dependencies and cannot be deleted`, бо
> балансувальники й ENI, створені Kubernetes, тримають підмережі. Terraform про
> них не знає: їх немає в `terraform.tfstate`.
>
> Те саме одним рядком робить `make cluster-down` — він питає підтвердження і
> нагадує про `make down`, але **сам його не викликає**. Порядок на вас.

### Крок 1: спершу знести те, що створив Kubernetes

```bash
# nginx із розділу 10 — саме він створив Classic Load Balancer
kubectl delete deploy/nginx-demo svc/nginx-demo --ignore-not-found

# І окремо — ДИСКИ. Їх створив CSI-драйвер, у tfstate їх немає,
# тож terraform destroy про них не знає і залишить їх у рахунку.
kubectl delete pvc --all -A
```

Чому саме зараз, а не «Terraform сам усе прибере»:

- `Service type=LoadBalancer` створив **Classic Load Balancer** в AWS;
- цей LB створив **Kubernetes**, тому в `terraform.tfstate` його немає;
- `terraform destroy` про нього не знає і не видалить;
- але LB тримає **мережеві інтерфейси в підмережах**, тому **видалити VPC теж не
  вийде**;
- результат: `destroy` крутиться ~20 хвилин і падає з
  `DependencyViolation: The subnet has dependencies and cannot be deleted`.

### Крок 2: переконатись, що LB зник

```bash
aws elb describe-load-balancers --region eu-central-1 \
  --query 'LoadBalancerDescriptions[].LoadBalancerName'
```

Має бути `[]`. Зазвичай зникає за 1–2 хвилини. Якщо ставили ще щось —
переконайтесь, що знесли **всі** Service типу LoadBalancer:

```bash
kubectl get svc -A | grep LoadBalancer     # має бути порожньо
```

Те саме про диски — `available` означає «нічий, але платний»:

```bash
aws ec2 describe-volumes --region eu-central-1 \
  --filters Name=status,Values=available --query 'Volumes[].VolumeId'
```

Має бути `[]`. Забутий 8 GiB `gp3` тихо тягне **$0.76/міс роками** — це
найпоширеніша «привидна» стаття рахунку після курсу.

### Крок 3: `terraform destroy`

```bash
cd terraform/cluster
terraform destroy
```

Те саме з підтвердженням і нагадуванням про порядок: `make cluster-down`.

Прочитайте план (`62 to destroy`), введіть **`yes`**. Триває **~10–15 хвилин**.

```
Destroy complete! Resources: 62 destroyed.
```

### Крок 4: чек-лист «нічого не залишилось»

Terraform міг не знести те, що створювали не він. Перевірте:

```bash
export R=eu-central-1

echo "— EKS кластери (має бути []):"
aws eks list-clusters --region $R --query clusters

echo "— EC2 інстанси (running):"
aws ec2 describe-instances --region $R \
  --filters "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId'

echo "— Load Balancers (classic + v2):"
aws elb  describe-load-balancers --region $R --query 'LoadBalancerDescriptions[].LoadBalancerName'
aws elbv2 describe-load-balancers --region $R --query 'LoadBalancers[].LoadBalancerName'

echo "— NAT Gateways (не deleted) — $0.052/год кожен:"
aws ec2 describe-nat-gateways --region $R \
  --filter "Name=state,Values=available,pending" --query 'NatGateways[].NatGatewayId'

echo "— Elastic IP — платні, якщо ні до чого не привʼязані:"
aws ec2 describe-addresses --region $R --query 'Addresses[].PublicIp'

echo "— EBS диски (available = нічий, але платний):"
aws ec2 describe-volumes --region $R \
  --filters "Name=status,Values=available" --query 'Volumes[].VolumeId'

echo "— VPC (крім default):"
aws ec2 describe-vpcs --region $R \
  --query 'Vpcs[?IsDefault==`false`].VpcId'
```

Усе має бути `[]` або `null`. Те саме через консоль: **EC2 → Load Balancers**,
**EC2 → Volumes**, **VPC → NAT Gateways**, **VPC → Elastic IPs**.

### Крок 5: бюджет-алерт (зробіть це ОДИН раз назавжди)

AWS Console → **Billing and Cost Management** → **Budgets** → *Create budget* →
*Zero spend budget* або *Cost budget* з порогом **$5** → вкажіть email.

Приходить лист, щойно витрати перевищать поріг. Це найдешевша страховка від
«забув знести кластер на новорічні свята».

Подивитись поточні витрати: **Billing → Cost Explorer** (дані оновлюються з
затримкою до 24 годин, тож одразу після пари ви побачите $0).

---

## 14. Типові помилки

| Симптом | Причина | Як полагодити |
|---|---|---|
| `brew install tfenv` → `Cannot link... /opt/homebrew/bin/terraform already exists` | стоїть формула `terraform` | `brew uninstall terraform`, потім `brew install tfenv` |
| `terraform: command not found` після встановлення tfenv | не поставлена жодна версія | `tfenv install 1.15.8 && tfenv use 1.15.8` |
| `Terraform v1.5.7 does not match configured version` | tfenv не бачить `.terraform-version` | ви не в теці `terraform/cluster/`. `cd terraform/cluster` |
| `Unable to locate credentials` | немає AWS-ключів | `aws configure`, тоді `aws sts get-caller-identity` |
| Terraform ходить не в той акаунт | активний не той профіль | `export AWS_PROFILE=потрібний`; перевірити `aws sts get-caller-identity` |
| `Error: Unsupported argument: cluster_name` | ви скопіювали код для модуля **v20**, у нас **v21** | `cluster_name`→`name`, `cluster_version`→`kubernetes_version` — див. таблицю в 6.5 |
| `UnauthorizedOperation` / `AccessDenied` при `apply` | IAM-юзеру бракує прав | для навчання — `AdministratorAccess` |
| `AddressLimitExceeded` | ліміт 5 Elastic IP на регіон | звільнити зайві EIP або запросити збільшення ліміту |
| `InvalidParameterException: Subnets must be in at least two AZs` | у `local.azs` менше 2 зон | перевірте `slice(..., 0, 2)` |
| `kubectl`: `You must be logged in to the server (Unauthorized)` | не виконано `update-kubeconfig` **або** немає `enable_cluster_creator_admin_permissions` | `aws eks update-kubeconfig ...`; перевірте прапорець у `main.tf` |
| `kubectl`: `Unable to connect to the server: dial tcp: i/o timeout` | `endpoint_public_access = false` або доступ обмежено по CIDR | перевірте `endpoint_public_access` та `endpoint_public_access_cidrs` |
| Ноди в статусі `NotReady` довше 5 хв | не піднявся CNI, часто через відсутність NAT | `kubectl describe node`; перевірте `enable_nat_gateway = true` |
| Под вічно в `Pending` | на нодах немає вільних ресурсів **або** скінчились IP | `kubectl describe pod <name>` → секція `Events` |
| PVC вічно в `Pending`, в `Events` **тиша** (жодного провізіонера) | клас `gp2` з вилученим `kubernetes.io/aws-ebs` — його не обробляє ніхто | зняти дефолт з `gp2`, застосувати `deploy/0-storage/storageclass-gp3.yaml` — [розділ 6.5](#сховище-ebs-csi-driver-і-чому-дефолтний-gp2-мертвий) |
| PVC у `Pending`, у `Events` — `AccessDenied` від `ebs.csi.aws.com` | addon стоїть, а IAM-роль ні (або trust policy не на `pods.eks.amazonaws.com`) | перевірте `terraform/cluster/ebs-csi-iam.tf` і `aws eks list-pod-identity-associations --cluster-name mlops-demo` |
| Под із PVC у `Pending`: `node(s) had volume node affinity conflict` | EBS-том в одній AZ, вільна нода — в іншій | `volumeBindingMode: WaitForFirstConsumer` у класі; повернути ноду в ту саму AZ або перестворити PVC |
| `StorageClass.provisioner: Invalid value: field is immutable` | ви намагаєтесь полагодити `gp2` | не лікується — тільки **новий** клас з новим імʼям |
| `terraform apply` не змінює кількість нод, хоч `node_desired_size` інший | модуль ігнорує зміни `desired_size` для існуючої групи | `aws eks update-nodegroup-config ... --scaling-config desiredSize=3` (команда в `main.tf`) |
| `plan` показує зміну `release_version` у node group | ви підняли `node_ami_release_version` — або хтось прибрав `use_latest_ami_release_version = false`, і модуль знову тягне найсвіжіший AL2023 | це rolling replacement **усіх** нод (~10 хв). Робіть свідомо і **до** створення PVC |
| `Requested release version 1.33.x is not valid for kubernetes version 1.34` | `node_ami_release_version` не збігається з `kubernetes_version` | привести перші дві цифри до однакових; чинна версія: `aws ssm get-parameter --name /aws/service/eks/optimized-ami/1.34/amazon-linux-2023/x86_64/standard/recommended/release_version --region eu-central-1 --query Parameter.Value --output text` |
| `Too many pods` на ноді | ліміт подів на інстанс (t3.medium ≈ 17) | більший тип інстанса або більше нод |
| `EXTERNAL-IP` вічно `<pending>` | немає тегів `kubernetes.io/role/elb` на підмережах | перевірте `public_subnet_tags` у `module "vpc"` |
| `destroy` падає з `DependencyViolation ... subnet has dependencies` | залишився Load Balancer, створений Kubernetes | `make down` (або `kubectl delete deploy/nginx-demo svc/nginx-demo`), зачекати 2 хв, повторити `destroy` |
| `Error acquiring the state lock` | попередній `apply` не завершився коректно | переконатись, що ніхто не працює паралельно, тоді `terraform force-unlock <LOCK_ID>` |
| `kubectl`: `unknown flag: --cpu-percent` / `--requests` | прапорці прибрані у нових версіях kubectl | `--cpu-percent` → `--cpu`; замість `kubectl run --requests` описуйте ресурси в YAML |
| `kubectl` лається на неіснуючий кластер, хоча ви його знесли | у `~/.kube/config` лишився старий контекст | `kubectl config delete-context <імʼя>` |
| `apply` завис на 20+ хв на EKS | так буває, це нормально | control plane створюється 9–10 хв. Не переривайте |

---

## Що далі

### ➡️ [Тема 6 — Чотири способи деплою](06-deploy-methods.md)

Кластер піднято. Наступний крок — **як саме** доставляти в нього застосунки.
Один і той самий nginx, задеплоєний чотирма способами, кожен зі своєю
кольоровою сторінкою:

| Спосіб | Що показує |
|---|---|
| `kubectl apply` | базу: ConfigMap, Deployment, Service вручну |
| **Helm** | шаблони + `values.yaml`, `upgrade` і `rollback` |
| **Kustomize** | dev/prod з однієї бази, без копіпасту |
| **ArgoCD** | GitOps: `git push` замість `kubectl`, self-heal |

### Інше

- **Karpenter** — розумніший autoscaler нод замість Cluster Autoscaler
- **IRSA / Pod Identity** — ми вже користуємось Pod Identity для драйвера дисків
  (`terraform/cluster/ebs-csi-iam.tf`); той самий патерн дає IAM-права будь-якому поду
- **AWS Load Balancer Controller** — сучасні ALB/NLB замість Classic LB
- **AWS Secrets Manager / SSM Parameter Store** — секрети (слайд 32)

## Корисні посилання

| Ресурс | Посилання |
|---|---|
| tfenv | https://github.com/tfutils/tfenv |
| Модуль EKS | https://registry.terraform.io/modules/terraform-aws-modules/eks/aws/latest |
| Модуль VPC | https://registry.terraform.io/modules/terraform-aws-modules/vpc/aws/latest |
| Гайд міграції v20 → v21 | https://github.com/terraform-aws-modules/terraform-aws-eks/blob/master/docs/UPGRADE-21.0.md |
| Документація EKS | https://docs.aws.amazon.com/eks/latest/userguide/ |
| Шпаргалка kubectl | https://kubernetes.io/docs/reference/kubectl/quick-reference/ |
| Калькулятор цін AWS | https://calculator.aws/ |
| K9s (TUI для кластера, слайд 33) | https://k9scli.io/ |
| Lens (GUI, слайд 33) | https://k8slens.dev/ |

---

## Версії, на яких усе перевірено

| Компонент | Версія |
|---|---|
| tfenv | 3.2.2 |
| Terraform | 1.15.8 |
| Провайдер `hashicorp/aws` | 6.60.0 |
| Модуль `terraform-aws-modules/eks/aws` | 21.24.1 |
| Модуль `terraform-aws-modules/vpc/aws` | 6.6.1 |
| Kubernetes (EKS) | 1.34 |
| kubectl | 1.34.1 |
| AWS CLI | 2.31.15 |

Перевірено 3 серпня 2026, регіон `eu-central-1`: `terraform plan` →
**62 ресурси, 0 помилок**.

Сховище (EBS CSI driver + StorageClass `gp3`) додано і перевірено 17 серпня 2026
на живому кластері: `plan` проти наявного стейту дає рівно
**4 to add, 1 to change, 0 to destroy**.
Addon `aws-ebs-csi-driver` версії `v1.63.1-eksbuild.1`.
