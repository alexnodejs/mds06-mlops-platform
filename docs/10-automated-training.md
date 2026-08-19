# Тема 10 — Автоматизоване тренування моделей

> Матеріал до заняття «Автоматизоване тренування моделей. GitLab CI та AWS Step Functions».
>
> **Одна відмінність від слайдів:** ми беремо **GitHub Actions**, а не GitLab CI.
> Механіка ідентична — OIDC-токен, `assume-role`, `aws stepfunctions start-execution`.
> Відповідність рядок-у-рядок є в розділі [8](#8-те-саме-на-gitlab-ci-слайди-29-30),
> щоб слайд і репозиторій не розходились.

---

## Зміст

- [0. Що ми будуємо](#0-що-ми-будуємо)
- [1. Навіщо це взагалі](#1-навіщо-це-взагалі-слайди-6-10)
- [2. Чому саме Step Functions](#2-чому-саме-step-functions-слайди-11-14-18)
- [3. Наш пайплайн: шість станів](#3-наш-пайплайн-шість-станів)
- [4. Де що лежить](#4-де-що-лежить)
- [5. Запуск](#5-запуск)
- [6. Quality gate — головна ідея теми](#6-quality-gate--головна-ідея-теми)
- [7. OIDC: доступ без ключів](#7-oidc-доступ-без-ключів-слайди-31-32)
- [8. Те саме на GitLab CI](#8-те-саме-на-gitlab-ci-слайди-29-30)
- [9. Сценарій заняття](#9-сценарій-заняття)
- [10. Типові помилки](#10-типові-помилки)
- [11. Прибирання](#11-прибирання)

---

## 0. Що ми будуємо

До цієї теми модель тренували руками: `make train` — і чекаємо. Тепер тренування
стає **подією в системі**, а не дією людини.

```
   git push у main
        │
        ▼
   GitHub Actions ──OIDC──► AWS         ключів немає, лише підписаний токен
        │
        ▼
   Step Functions «mds06-train»
        │
        ├─ 1. ValidateParams   Lambda    параметри з CI осмислені?      200 мс
        │
        ├─ 2. TrainOnEKS       Job       тренування в кластері,         ~90 с
        │                                метрики й модель у MLflow
        │
        ├─ 3. EvaluateModel    Lambda    нова f1 проти чинної           50 мс
        │
        ├─ 4. Choice ──── краща ──► PromoteModel   Job: @champion + /reload
        │           └── не краща ──┐
        │                          │
        ├─ 5. LogMetrics       Lambda ◄─┘  підсумок у CloudWatch
        │
        └─ 6. Promoted / ModelRejected      обидва — Succeed
```

Наприкінці на http://localhost:8000 або та сама модель, що була, або нова —
залежно від того, що вирішив крок 4. **Ніхто не заходив у кластер руками.**

---

## 1. Навіщо це взагалі (слайди 6-10)

Слайд 10 ставить питання: чому ручне тренування швидко стає проблемою?

Ручний запуск — це не «повільно». Це **невідтворювано**:

| Ручне тренування | Автоматизоване |
|---|---|
| «У мене вийшло 0.96» — на яких даних, з якими параметрами? | Параметри й метрики в MLflow, привʼязані до коміта |
| Модель у проді ≠ модель у чиємусь ноутбуці | У прод їде лише те, що пройшло пайплайн |
| Хто останній перезаписав `@champion`? | Аліас переставляє тільки крок PromoteModel |
| Новачок відтворює середовище три дні | `make pipeline-run` |

Слайд 8 називає шість кроків. Ось де кожен у нашому пайплайні:

| Слайд 8 | У нас |
|---|---|
| 1. Тригер події | `push` у `main` або ручний запуск з UI GitHub |
| 2. Підготовка середовища | Job у Kubernetes із готового образу |
| 3. Завантаження даних | `load_iris()` — навчальний датасет уже в образі |
| 4. Запуск скрипту тренування | `python train.py` із гіперпараметрами з CI |
| 5. Збереження артефактів | MLflow: PostgreSQL — метрики, MinIO — модель і графіки |
| 6. Фіксація статусу | Lambda `log_metrics` → CloudWatch, плюс код виходу CI |

> **Крок 3 у нас найслабший, і це свідомо.** Датасет Iris зашитий у образ, тож
> «нових даних» не буває — тренування завжди дає приблизно те саме. Реальний
> пайплайн тягнув би батч з S3, і тоді quality gate ловив би деградацію на нових
> даних. Як це виглядає — розділ [9](#9-сценарій-заняття), варіант «своя вправа».

---

## 2. Чому саме Step Functions (слайди 11-14, 18)

Слайд 20 питає: чому не звичайний bash-скрипт?

Скрипт зробив би те саме — рівно один раз і на одній машині. Різниця зʼявляється,
коли щось іде не так:

| | bash-скрипт | Step Functions |
|---|---|---|
| Крок упав посередині | усе спочатку | `Retry` на конкретному кроці, `Catch` веде в окрему гілку |
| Де ми зараз | `echo` у логах | граф у консолі, видно поточний стан і всі входи-виходи |
| Умовна логіка | `if` у коді, який ніхто не читає | `Choice` видно на схемі |
| Тренування 40 хвилин | процес CI тримає раннер | Step Functions чекає сам, CI може відпустити |
| Хто це запускав і з чим | historia bash_history | історія виконань зі входом кожного |

Ключова теза слайда 18: **Step Functions нічого не обчислюють**. Вони вирішують,
*коли* і *що* запустити і *що робити з помилкою*. Обчислення — у Lambda та в поді
Kubernetes.

---

## 3. Наш пайплайн: шість станів

Повне визначення — [`terraform/training-pipeline/state_machine.asl.json`](../terraform/training-pipeline/state_machine.asl.json).
Його можна вставити у Workflow Studio в консолі AWS і побачити граф.

### 1. ValidateParams — Lambda

[`lambdas/validate/handler.py`](../lambdas/validate/handler.py)

Перевіряє, що з CI прилетіли осмислені гіперпараметри: числа в межах, сітка не
більша за 12 запусків, імʼя експерименту безпечне, `commit_sha` схожий на SHA.

**Чому першим.** Наступний крок піднімає под у Kubernetes. `n_estimators="сто"`
без цієї перевірки виявиться через дві хвилини — з логів пода, який упав на
`int()`. Із нею — за 200 мс, до того як витрачено хоч один ресурс. Це загальне
правило пайплайнів: **найдешевша перевірка йде першою**.

### 2. TrainOnEKS — `eks:runJob.sync`

Той самий Job, що й у `make train`, але параметри підставляє Step Functions.
`.sync` означає «чекати, поки Job завершиться».

```json
"Resource": "arn:aws:states:::eks:runJob.sync",
"Parameters": {
  "ClusterName": "mlops-demo",
  "CertificateAuthority": "<base64 CA кластера>",
  "Endpoint": "https://....eks.amazonaws.com",
  "Namespace": "mlflow",
  "LogOptions": { "RetrieveLogs": true, "LogParameters": { "tailLines": ["40"] } },
  "Job": { ... звичайний batch/v1 Job ... }
}
```

Три речі, які варто розуміти:

- **Step Functions ходить прямо в Kubernetes API**, а не через AWS API. Тому
  дозволи тут дає не політика IAM, а Access Entry — див. розділ
  [7](#7-oidc-доступ-без-ключів-слайди-31-32) і файл `access-entry.tf`.
- **Працює лише з публічним endpoint** API-сервера. Наш кластер саме такий.
  Якщо колись звузити `endpoint_public_access_cidrs` до офісних IP — Тема 10
  зламається, бо діапазонів Step Functions AWS не публікує.
- **`RetrieveLogs: true` віддає stdout пода** у полі `logs` результату. Саме
  так наступний крок дізнається метрики, не ходячи в MLflow.

Тренування запускається з `PROMOTE_TO_CHAMPION=false`: модель **реєструється**
новою версією, але аліас `@champion` не чіпає. Рішення ухвалює gate.

### 3. EvaluateModel — Lambda

[`lambdas/evaluate/handler.py`](../lambdas/evaluate/handler.py)

Розбирає логи пода, знаходить останню подію `training_result` і порівнює `f1`
нової моделі з `f1` чинної.

**Чому Lambda, а не ще один Job.** MLflow живе за ClusterIP-сервісом — ззовні
кластера до нього не достукатись навіть із Lambda в тому самому VPC. Тому ми
туди й не ходимо: обидва числа вже надрукував тренувальний под. Lambda лишається
**чистою функцією без мережі й без прав**, а це найпростіше, що можна
налагоджувати і тестувати.

Самоперевірка без AWS:

```bash
cd lambdas/evaluate && python3 test_handler.py
```

### 4. Choice — quality gate

```json
"PromoteOrReject": {
  "Type": "Choice",
  "Choices": [{ "Variable": "$.evaluation.promote", "BooleanEquals": true, "Next": "PromoteModel" }],
  "Default": "LogMetrics"
}
```

`Default` обовʼязковий. Без нього неспівпадіння дає `States.NoChoiceMatched` і
виконання падає — класична помилка при першому написанні ASL.

### 5. PromoteModel — знову `eks:runJob.sync`

[`apps/trainer/promote.py`](../apps/trainer/promote.py) у тому самому образі.
Перевішує аліас `@champion` і робить `POST /reload` у сервіс моделі.

Тут Job, а не Lambda, з тієї ж причини: і MLflow, і сервіс моделі доступні лише
зсередини кластера.

Помилка `/reload` **не валить** промоцію: аліас уже переставлено, а сервіс
перечитає реєстр сам протягом 30 секунд. Сервіс міг узагалі не бути піднятий —
це не привід відкочувати реєстр.

### 6. LogMetrics — Lambda, і два Succeed

[`lambdas/log_metrics/handler.py`](../lambdas/log_metrics/handler.py) пише в
CloudWatch (namespace `MDS06/Training`) метрики `F1`, `Accuracy`, `Promoted`,
`F1Delta`.

Навіщо дублювати те, що вже є в MLflow: MLflow відповідає на питання «яка модель
краща», CloudWatch — «чи здоровий сам пайплайн». Алерт «третій прогін поспіль
нічого не промоутить» ставиться на CloudWatch і не залежить від того, чи піднятий
MLflow.

**Обидві гілки сходяться сюди.** Прогін, який нічого не промоутив, теж мусить
лишити слід.

> **`ModelRejected` — це `Succeed`, а не `Fail`.**
> Це не дрібниця стилю. Червоне виконання означає **зламаний пайплайн**.
> Відхилена модель — це пайплайн, який спрацював рівно так, як мав: подивився
> на числа й вирішив не чіпати прод. Якби це був `Fail`, за два тижні команда
> навчилася б ігнорувати червоне.

---

## 4. Де що лежить

```
lambdas/
├── validate/handler.py          перевірка параметрів
├── evaluate/handler.py          quality gate
├── evaluate/test_handler.py     самоперевірка gate без AWS
└── log_metrics/handler.py       підсумок у CloudWatch

terraform/training-pipeline/
├── main.tf                      data-джерела, locals
├── variables.tf                 усе, що можна налаштувати
├── iam.tf                       три ролі: SFN, Lambda, GitHub
├── access-entry.tf              ⭐ доступ SFN усередину кластера
├── lambdas.tf                   три функції + ZIP + лог-групи
├── sfn.tf                       state machine
├── github-oidc.tf               автентифікація без ключів
├── state_machine.asl.json       визначення пайплайну
└── outputs.tf

.github/workflows/train.yml      тригер із CI
scripts/pipeline-run.sh          той самий запуск, але з термінала
apps/trainer/promote.py          промоція всередині кластера
```

---

## 5. Запуск

### Передумови

Стек Тем 8-9 має бути піднятий: `make up`. Пайплайну потрібні namespace `mlflow`,
Secret `mlflow-credentials` і працюючий MLflow.

### Розгортання

```bash
make pipeline-up
```

Створює 17 ресурсів: 3 Lambda з лог-групами, state machine, 3 ролі IAM, Access
Entry у кластері. Наприкінці друкує ARN, які треба підставити у workflow — це
робить `make init`.

### Перший запуск

```bash
make pipeline-run
```

Друкує посилання на граф у консолі AWS і показує, на якому кроці зараз:

```
── запускаю cli-20260819-143022 ──
   ▸ ValidateParams
   ▸ TrainOnEKS
   ▸ EvaluateModel
   ▸ PromoteModel
   ▸ LogMetrics

   ✅ ПРОМОУТ  —  f1 0.9667 проти 0.9333 у чинної: приріст +0.0333 ≥ поріг 0.001
      версія моделі: 7   f1: 0.9667
```

### Запуск із CI

```bash
git commit --allow-empty -m "перетренувати" && git push
```

Або з UI GitHub: **Actions → Тренування моделі → Run workflow** — там поля для
гіперпараметрів. Саме цей шлях зручний на занятті: змінюєте числа й одразу
бачите інший вердикт.

---

## 6. Quality gate — головна ідея теми

Тренування, яке завжди публікує результат, — це не автоматизація, а автоматичне
псування прода. Різниця в одному рядку:

```python
promote = (f1_нової - f1_чинної) >= MIN_DELTA
```

`MIN_DELTA = 0.001` (змінна `min_delta` у Terraform). Нуль тут не годиться:
RandomForest використовує випадковість, і дві однакові конфігурації дають різницю
в четвертому знаку. З нульовим порогом прод перекочувався б на кожному запуску
без жодної користі — і кожен такий перекат це ризик.

Чого gate **не** робить у цьому навчальному прикладі, а в житті мав би:

- не дивиться на **окремі класи** — модель може підняти середню f1 і водночас
  провалити один клас;
- не має **абсолютної підлоги** (`f1 >= 0.9` незалежно від чинної) — ланцюжок
  дрібних «покращень» може повільно з'їхати вниз;
- не перевіряє модель на **замороженому holdout**, який не бачив жодного
  тренувальний прогін.

Це готові теми для вправ — див. [exercises.md](exercises.md).

---

## 7. OIDC: доступ без ключів (слайди 31-32)

Слайд 31 дає два варіанти. Різниця на практиці:

| | Ключі в змінних CI | OIDC |
|---|---|---|
| Що зберігається в репозиторії | `AWS_SECRET_ACCESS_KEY` назавжди | нічого |
| Витік | доступ до акаунта, доки не помітять | красти нічого |
| Термін дії | безстроково | одна година, на один job |
| Обмеження | ролі й політики | ще й репозиторій + гілка |

Слайд 32 називає це «одноразовим пропуском з печаткою» — механіка саме така:

1. GitHub видає job підписаний JWT з полем `sub`, у якому репозиторій і гілка.
2. AWS перевіряє підпис відкритим ключем GitHub.
3. Дивиться `sub` і `aud` і звіряє з умовами trust policy ролі.
4. Видає тимчасові креденшели на годину.

У `github-oidc.tf` умова виглядає так:

```hcl
condition {
  test     = "StringLike"
  variable = "token.actions.githubusercontent.com:sub"
  values = [
    "repo:alexnodejs/mds06-mlops-platform:ref:refs/heads/main",
    "repo:alexnodejs@*/mds06-mlops-platform@*:ref:refs/heads/main",
  ]
}
```

Два формати, бо GitHub у липні 2026 перейшов на **незмінний** `sub`, куди додає
числові ID власника й репозиторію. Репозиторії, створені після цієї дати,
надсилають другий формат; старі — перший. Зірочки закривають лише числові ID:
власник, назва репозиторію і гілка лишаються прибитими намертво.

**Роль може рівно дві речі:** запустити ОДНУ конкретну state machine і подивитись
статус її виконань. Навіть якщо хтось перепише workflow, більше нічого в акаунті
з цим токеном зробити не вийде.

### ⚠️ Дозволи в AWS і дозволи в Kubernetes — різні системи

Найчастіша помилка Теми 10 — `EKS.401` на кроці `TrainOnEKS`.

`eks:runJob` **не викликає AWS API**. Step Functions відкриває HTTPS до
Kubernetes API кластера і авторизується там як IAM-принципал. Хто цей принципал
усередині кластера, вирішує вже Kubernetes. Тому в ролі Step Functions **немає
жодного дозволу `eks:*`** — і додавати їх безглуздо, помилка від цього не зникне.

Доступ дає Access Entry (`access-entry.tf`):

```hcl
resource "aws_eks_access_policy_association" "sfn" {
  policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"
  access_scope {
    type       = "namespace"      # ⭐ не "cluster"
    namespaces = ["mlflow"]
  }
}
```

Роль може створювати Job рівно в `mlflow` і ніде більше. Із `type = "cluster"`
та сама політика дала б їй право правити будь-що в `kube-system`.

Перевірити, що доїхало:

```bash
aws eks list-associated-access-policies --cluster-name mlops-demo \
  --principal-arn "$(cd terraform/training-pipeline && terraform output -raw sfn_role_arn)"
```

---

## 8. Те саме на GitLab CI (слайди 29-30)

Слайди показують GitLab. Ось відповідність — вся різниця в трьох рядках.

| | GitHub Actions | GitLab CI |
|---|---|---|
| Запит токена | `permissions: id-token: write` | `id_tokens: { GITLAB_OIDC_TOKEN: { aud: https://gitlab.com } }` |
| Обмін на креденшели | `aws-actions/configure-aws-credentials@v5` | `aws sts assume-role-with-web-identity --web-identity-token "$GITLAB_OIDC_TOKEN"` |
| Провайдер у AWS | уже існує в акаунті | `aws_iam_openid_connect_provider` з `url = "https://gitlab.com"` |
| Умова в trust policy | `repo:owner/repo:ref:refs/heads/main` | `project_path:group/project:ref_type:branch:ref:main` |
| Унікальне імʼя запуску | `${run_id}-${run_attempt}` | `${CI_PIPELINE_ID}-${CI_JOB_ID}` |

Сам крок запуску — слово в слово той самий, що на слайді 30:

```yaml
trigger-training:
  image: { name: public.ecr.aws/aws-cli/aws-cli:latest, entrypoint: [""] }
  id_tokens:
    GITLAB_OIDC_TOKEN: { aud: https://gitlab.com }
  script:
    - |
      CREDS=$(aws sts assume-role-with-web-identity \
        --role-arn "$AWS_ROLE_ARN" \
        --role-session-name "gitlab-${CI_PIPELINE_ID}" \
        --web-identity-token "$GITLAB_OIDC_TOKEN" \
        --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text)
      export AWS_ACCESS_KEY_ID=$(echo "$CREDS" | cut -f1)
      export AWS_SECRET_ACCESS_KEY=$(echo "$CREDS" | cut -f2)
      export AWS_SESSION_TOKEN=$(echo "$CREDS" | cut -f3)
    - |
      aws stepfunctions start-execution \
        --state-machine-arn "$STATE_MACHINE_ARN" \
        --name "ci-${CI_PIPELINE_ID}-${CI_JOB_ID}" \
        --input "{\"commit_sha\":\"${CI_COMMIT_SHA}\"}"
```

⚠️ Два місця, де GitLab відрізняється боляче:

- `aud` мусить **точно** збігатися з `client_id_list` провайдера. За замовчуванням
  GitLab ставить `aud` = URL інстансу (`https://gitlab.com`), а не
  `sts.amazonaws.com`. Розбіжність дає `InvalidIdentityToken`.
- Пайплайни merge request дають `sub` з `ref_type:merge_request_ref` — вони
  **не пройдуть** умову з `ref_type:branch`. Це навмисно: тренувати з чужої
  гілки не треба.

---

## 9. Сценарій заняття

Три запуски, три різні уроки. Усе через `make pipeline-run` — без комітів.

### Прогін 1 — базовий

```bash
make pipeline-run
```
Перша модель у реєстрі → `ПРОМОУТ`. Показати граф у консолі AWS: усі шість станів
зелені, видно вхід і вихід кожного.

### Прогін 2 — свідомо гірша модель

```bash
make pipeline-run N=10 D=1
```
Десять дерев глибиною 1 — модель гірша за чинну. Graf іде в `ModelRejected`.

Показати три речі:
1. Виконання **SUCCEEDED**, не FAILED. Пайплайн спрацював як мав.
2. У MLflow нова версія **зʼявилась**, але аліас `@champion` лишився на старій.
3. http://localhost:8000 показує **стару** версію. Прод не постраждав.

### Прогін 3 — краща модель

```bash
make pipeline-run N=300,500 D=none
```
`ПРОМОУТ`. Оновити http://localhost:8000 — номер версії змінився без жодного
`kubectl`. Це і є замикання кола: Тема 8 показувала модель, Тема 9 — реєстр,
Тема 10 звʼязала їх пайплайном.

### Прогін 4 — зламати навмисно

```bash
make pipeline-run N=сто
```
`ValidateParams` кидає помилку → `ParamsRejected` за 200 мс. Порівняти з тим,
скільки часу це коштувало б без першого кроку.

### Своя вправа

Зробити gate суворішим: додати абсолютну підлогу `f1 >= 0.9` незалежно від
чинної моделі. Правити один файл — `lambdas/evaluate/handler.py`, перевірити
`python3 test_handler.py`, застосувати `make pipeline-up`.

---

## 10. Типові помилки

| Симптом | Причина | Що робити |
|---|---|---|
| `EKS.401 Unauthorized` на TrainOnEKS | Access Entry не доїхала | `aws eks list-associated-access-policies --cluster-name mlops-demo --principal-arn <роль SFN>` |
| `EKS.404` на TrainOnEKS | немає namespace `mlflow` | `make up` |
| `States.DataLimitExceeded` | логи пода не влізли в 256 KiB | зменшити `tailLines` у `state_machine.asl.json` |
| `EvaluationFailed`, «немає training_result» | забрали замало рядків логів або `train.py` упав до кінця | подивитись повні логи: `kubectl -n mlflow logs -l job-name=train-...` |
| `EKS.409 AlreadyExists` | Job із таким іменем ще не прибрався | минеться саме: `Retry` + `ttlSecondsAfterFinished` |
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | `sub` у токені не збігся з умовою | перевірити формат `sub`: старий чи незмінний (розділ 7) |
| `InvalidIdentityToken` | `aud` не збігся з `client_id_list` | для GitHub має бути `sts.amazonaws.com` |
| `EntityAlreadyExists` на OIDC-провайдері | провайдер уже є в акаунті | має бути `data`, не `resource` — так і зроблено |
| `StateMachineDoesNotExist` у CI | `STATE_MACHINE_ARN` не підставлено | `make init` або вручну в `.github/workflows/train.yml` |
| Виконання «висить» на TrainOnEKS 2 хв | так і має бути | Step Functions опитує статус Job раз на хвилину |
| `ExecutionAlreadyExists` при re-run | імʼя виконання повторилось | у workflow вже є `run_attempt`; локально імʼя з міткою часу |

---

## 11. Прибирання

```bash
make pipeline-down     # Lambda, state machine, ролі, Access Entry
```

Кластер і стек Тем 8-9 не чіпає. Пайплайн можна зносити й піднімати скільки
завгодно — він нічого не зберігає, увесь стан у MLflow.

Що лишається після `pipeline-down` і прибирається саме:
- лог-групи CloudWatch — `retention_in_days = 14`;
- метрики в `MDS06/Training` — 15 місяців, безкоштовно;
- Job у кластері — `ttlSecondsAfterFinished`.

---

## Версії, на яких перевірено

| Що | Версія |
|---|---|
| Terraform | 1.15.8 |
| provider aws | ~> 6.52 |
| Lambda runtime | python3.13 |
| Step Functions | STANDARD, JSONPath (не JSONata) |
| EKS | 1.34 |
| `aws-actions/configure-aws-credentials` | v5 |
