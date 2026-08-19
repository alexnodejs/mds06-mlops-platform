# MLflow + виявлення дріфту в Kubernetes

**Тема 9. Моніторинг якості моделей та відстеження експериментів** — практична частина
Курс MLOps CI/CD 2.0

MLflow (tracking server + Model Registry) з PostgreSQL як backend store і MinIO
як сховище артефактів, усе розгорнуте **декларативно через ArgoCD з Helm-чартів**
(слайд 17). Плюс власний експортер дріфту: KS-тест і хі-квадрат → Prometheus →
Grafana (слайди 34-36).

Модель — та сама **Iris RandomForest із Теми 8**. Тема 9 нічого в ній не
переписує: дріфт рахується з логів, які модель уже пише в Loki.

> ### Чесно про статус перевірки
>
> **Перевірено локально, з реальними числами:** `helm template` для всіх трьох
> чартів рендериться без помилок; калібрування порогів прогнано на 200 вікнах по
> 3000 записів; `drift_exporter.py` має пройдені тести (логіка + похід у
> фальшивий Loki по HTTP); вивід `/metrics` містить усі 6 контрактних метрик;
> `charts.bitnami.com/bitnami/postgresql-18.8.6.tgz` віддає **HTTP 403** —
> перевірено `curl`.
>
> **НЕ перевірено на живому кластері:** у сесії підготовки не було
> AWS-креденшелів (`aws sts get-caller-identity` → `Unable to locate
> credentials`). Тобто версії addon-ів, стан кластера і сам прогін розгортання
> ви перевіряєте самі — команди-гейти для цього нижче позначені 🚦.
> Це відрізняє цей README від README Теми 8, який прогнаний наживо.

---


## ⚠️ MLflow 3.x: чому UI виглядає порожнім

Найперше, на що наступають. Відкриваєте MLflow, а запусків немає — хоча
тренування пройшло і API їх бачить.

Причина: **MLflow 3.x розділив інтерфейс на два режими**, і за замовчуванням
може відкритися не той.

| `workflowType` | Що показує |
|---|---|
| `genai` | трейси LLM, промпти, споживання токенів — **порожньо, ми не робимо GenAI** |
| `machine_learning` | запуски, параметри, метрики, артефакти — **ось де наше** |

Правильне посилання:

```
http://localhost:5001/#/experiments/1?workflowType=machine_learning
```

Або перемкніть режим у верхньому переключачі з **GenAI** на **Machine Learning** —
UI запамʼятає вибір.

Перевірити, що дані насправді є, не залежачи від UI:

```bash
curl -s -X POST http://localhost:5001/api/2.0/mlflow/runs/search \
  -H 'Content-Type: application/json' \
  -d '{"experiment_ids":["1"],"max_results":20}' | python3 -m json.tool | head -30
```

> Слайди 31-33 показують інтерфейс MLflow 2.x, де такого поділу не було.
> Це та сама категорія розбіжності, що й решта в таблиці вище.


## 🔴 Головна проблема цієї теми: сховище зламане, і обійти більше не вийде

У Темах 5-8 будь-який PVC зависав у `Pending` назавжди, і це обходили через
`persistence.enabled=false` / `emptyDir`. Тема 9 вимагає MinIO і PostgreSQL —
обидва stateful. Учити «зберігайте моделі, щоб не втрачати роботу» на
`emptyDir` неможливо: перший рестарт пода знищить усі експерименти.

**Діагноз (три незалежні причини, кожної достатньо):**

1. Єдиний StorageClass `gp2` має `provisioner: kubernetes.io/aws-ebs` —
   in-tree плагін, **вилучений з Kubernetes у 1.31**. Кластер на 1.34.
2. Поле `provisioner` у StorageClass **іммутабельне**: полагодити `gp2`
   правкою неможливо в принципі, тільки новий обʼєкт з новим імʼям.
3. Драйвера `ebs.csi.aws.com` у кластері немає взагалі (є лише зареєстрований
   `efs.csi.aws.com` без файлової системи й без подів).

**Діагностичний відбиток, який варто показати студентам:** `kubectl describe
pvc` на класі `gp2` дає Pending, у якому в Events **немає ані**
`ProvisioningFailed`, **ані згадки жодного провізіонера** — максимум `waiting
for a volume to be created`. Тиша в подіях = ніхто не взявся за роботу.
Порівняйте з `gp3`, де відразу зʼявляються `Provisioning` /
`ProvisioningSucceeded` від `ebs.csi.aws.com_...`.

**Лікування** (у репозиторії `mds06-kuber-from-scratch`, бо це Terraform):
addon `aws-ebs-csi-driver` + `eks-pod-identity-agent`, IAM-роль
(`terraform/ebs-csi-iam.tf`) і новий StorageClass
`deploy/0-storage/storageclass-gp3.yaml` з `volumeBindingMode:
WaitForFirstConsumer`. Порядок і гейти — у розділі «Швидкий старт».

---

## Що піднімається

| Namespace | Компонент | Подів | RAM requests / limits |
|---|---|---|---|
| `mlflow` | PostgreSQL 18.6 (StatefulSet, PVC 8Gi) | 1 | 256Mi / 512Mi |
| `mlflow` | MinIO (standalone, PVC 8Gi) | 1 | 512Mi / 1Gi |
| `mlflow` | MLflow 3.15.1 (tracking + UI) | 1 | 256Mi / 768Mi |
| `mlflow` | drift-exporter | 1 | 64Mi / 256Mi |
| `kube-system` | ebs-csi-controller ×2 + ebs-csi-node ×2 | 4 | ~700Mi |
| `kube-system` | eks-pod-identity-agent (DaemonSet) | 2 | ~60Mi |
| | **разом додається** | **10** | **~1.1 GiB req** |

Плюс транзієнтні поди: `minio-post-job` (створює bucket через `mc`) і Job
тренування. Живуть хвилину і зникають — але слот пода їм потрібен.

```
                        ┌─ MinIO (S3) ──── PVC 8Gi gp3 ── EBS
   train.py ─────┤   bucket mlflow-artifacts
     log_param    (Job) │
     log_metric         └─ MLflow tracking ── PostgreSQL ── PVC 8Gi gp3 ── EBS
     log_artifact          (порівняння запусків,
     log_model              Model Registry)

   ml-model (Тема 8) ── stdout JSON ── Alloy ── Loki
                                                 │
                          drift-exporter ────────┘  (query_range, вікно 10 хв)
                            ks_2samp × 4 ознаки
                            chisquare × класи
                                 │
                                 └─ /metrics:9100 ── ServiceMonitor ── Prometheus ── Grafana
```

### ⚠️ Поди — вузьке місце, і воно бінд

Заміряно: 2 × t3.medium дають **34 слоти подів** (по 17 на ноду, ліміт ENI).

```
13  зайнято зараз (argocd 7 + kube-system 6)
11  стек Теми 8 (monitoring 6 + logging 2 + ml-demo 3)
 6  EBS CSI (4) + pod-identity-agent (2)
 4  Тема 9 (postgres, minio, mlflow, drift-exporter)
───
34 з 34 — НУЛЬ вільних, а потрібні ще 2 слоти на транзієнтні Job
```

**Що станеться без третьої ноди** (і виглядатиме це не як «немає подів», а як
«ArgoCD зламався»): post-install Job чарта MinIO не отримає слота → Job
Pending → `app-minio` назавжди `Progressing`, бо це PostSync-хук → хвиля 1
**ніколи не стартує** → MLflow не зʼявиться взагалі.

Памʼять і CPU при цьому вільні (61% і 42% від requests), тому спокуса
«зекономити слот, прибивши ліміти» не працює — це рівно ті граблі, через які в
Темі 8 сайдкари Grafana ловили OOMKilled.

**Рішення: третя нода.** `terraform apply -var node_desired_size=3` — +17
слотів, +$1.09/добу. Безкоштовна альтернатива (IRSA замість Pod Identity −2
поди, `kubectl scale deploy/ebs-csi-controller --replicas=1` −1,
`ml-model --replicas=1` −1) дає 30 з 34, тобто рівно на два Job без запасу на
rolling update.

---

## Швидкий старт

### 0. 🚦 Роззброїти міну в Terraform

```bash
cd /Users/alexandervasileyko/Repos/goit/mds06-kuber-from-scratch
git checkout terraform/variables.tf     # у робочій копії було desired=1, max=1
git diff --stat terraform/              # мусить бути порожньо по variables.tf
```

`terraform apply` з `node_max_size = 1` зсадить ASG до однієї ноди: −17 слотів,
−1930m CPU, −3.2 GiB, і `max_size=1` **забороняє відкат скейлом**. Найдорожча
помилка теми.

### 1. Сховище: addon + IAM + StorageClass

```bash
cd terraform && terraform init -upgrade
terraform plan -var node_desired_size=3 -out=tema9.tfplan
```

🚦 **Гейт: читати план очима.** Очікувано РІВНО чотири `+`
(`aws_iam_role.ebs_csi`, `aws_iam_role_policy_attachment.ebs_csi`,
`module.eks.aws_eks_addon.this["aws-ebs-csi-driver"]`,
`module.eks.aws_eks_addon.before_compute["eks-pod-identity-agent"]`) і `~` зміна
`desired_size 2 → 3`. Побачили `-/+`, `must be replaced`, будь-що про
`aws_launch_template` / `aws_eks_cluster` / перестворення node group — **СТОП**.

> `most_recent = true` — дефолт модуля для КОЖНОГО addon, тож план може
> запропонувати оновити ще й coredns / kube-proxy / vpc-cni. Це rolling-рестарт
> coredns (коротка DNS-турбулентність). Або приймаєте свідомо, або пініте
> `addon_version` усім чотирьом.

```bash
terraform apply tema9.tfplan            # ~8-10 хв: нода join ~3 хв, addon-и ~2-3 хв

# 🚦 перевірка — ДИВИТИСЬ КОЛОНКУ READY, а не STATUS
kubectl get pods -n kube-system -l app.kubernetes.io/name=aws-ebs-csi-driver
kubectl get csidrivers                  # має зʼявитись ebs.csi.aws.com
aws eks list-pod-identity-associations --cluster-name mlops-demo --region eu-central-1

# ПОРЯДОК ОБОВʼЯЗКОВИЙ: спершу зняти дефолт зі старого класу
kubectl annotate storageclass gp2 storageclass.kubernetes.io/is-default-class-
kubectl apply -f deploy/0-storage/storageclass-gp3.yaml
kubectl get sc                          # 🚦 РІВНО один рядок містить (default)

# димовий тест, 40 секунд
kubectl get pvc gp3-smoke -w            # Pending → Bound за ~15 с
kubectl logs pod/gp3-smoke              # ok
kubectl delete pod gp3-smoke; kubectl delete pvc gp3-smoke   # ПРИБРАТИ, це гроші
```

Два дефолтних StorageClass одночасно = недетермінований вибір: PVC може
приліпитись до мертвого `gp2` і зависнути, а виглядатиме це як «драйвер не
працює», хоча драйвер бездоганний.

### 2. Стек Теми 8 — ДО Теми 9

```bash
/Users/alexandervasileyko/Repos/goit/mds06-ml-monitoring/redeploy.sh
kubectl get crd | grep monitoring.coreos.com    # 🚦 CRD мусять існувати
```

Порядок не косметичний: `app-drift` містить ServiceMonitor, а CRD
`monitoring.coreos.com` приносить kube-prometheus-stack. Application із CR
падає **цілком**, якщо CRD ще немає.

### 3. Один Secret на всі креденшели (слайд 23)

```bash
kubectl create namespace mlflow --dry-run=client -o yaml | kubectl apply -f -

MINIO_PW=$(openssl rand -hex 16); PG_PW=$(openssl rand -hex 16); PG_SUPER=$(openssl rand -hex 16)
kubectl -n mlflow create secret generic mlflow-credentials \
  --from-literal=rootUser=mlflowadmin \
  --from-literal=rootPassword="$MINIO_PW" \
  --from-literal=AWS_ACCESS_KEY_ID=mlflowadmin \
  --from-literal=AWS_SECRET_ACCESS_KEY="$MINIO_PW" \
  --from-literal=POSTGRES_DB=mlflow \
  --from-literal=POSTGRES_USER=mlflow \
  --from-literal=POSTGRES_PASSWORD="$PG_PW" \
  --from-literal=POSTGRES_POSTGRES_PASSWORD="$PG_SUPER" \
  --from-literal=MLFLOW_TRACKING_URI=http://mlflow.mlflow.svc.cluster.local:80 \
  --from-literal=MLFLOW_S3_ENDPOINT_URL=http://minio.mlflow.svc.cluster.local:9000 \
  --from-literal=AWS_DEFAULT_REGION=eu-central-1 \
  --dry-run=client -o yaml | kubectl apply -f -
```

Секрет **поза Git і поза ArgoCD**: у нього немає лейбла
`argocd.argoproj.io/instance`, тож `prune: true` його не знесе. Та сама команда
з описом кожного ключа лежить у `k8s/secret-example.yaml` — і цей файл навмисно
**не** в `kustomization.yaml`: інакше ArgoCD застосував би заглушки поверх
реального обʼєкта, і MinIO отримав би пароль «ЗАМІНІТЬ...».

| Ключ | Хто читає |
|---|---|
| `rootUser`, `rootPassword` | MinIO. Імена **зашиті в чарт** minio 5.4.0, перейменувати не можна. Пароль ≥ 8 символів, інакше MinIO не стартує |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | MLflow-сервер + клієнт. Дублюють rootUser/rootPassword — root MinIO працює як S3-ключ |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Postgres (створює БД і юзера) + MLflow (`PGUSER`/`PGPASSWORD`) |
| `POSTGRES_POSTGRES_PASSWORD` | пароль суперюзера `postgres`. Офіційний образ без нього не стартує; MLflow ним не користується |
| `MLFLOW_TRACKING_URI`, `MLFLOW_S3_ENDPOINT_URL`, `AWS_DEFAULT_REGION` | ТІЛЬКИ клієнт, через `envFrom` — щоб у Job було 3 рядки YAML замість 20. Не секрети, лежать тут для зручності |

**Продакшн-альтернатива (слайд 23):** External Secrets Operator — `SecretStore`
(provider `aws.secretsManager`, авторизація через IRSA) + `ExternalSecret` з
`dataFrom: [{extract: {key: mds06/mlflow}}]`, який матеріалізує рівно цей самий
Secret з тими самими 11 ключами. Тоді **весь** репозиторій під GitOps: у Git
лежать лише посилання, значень немає. Vault + vault-secrets-operator — той
самий патерн, інший бекенд.

### 4. Образ інструментів у ECR

```bash
aws ecr create-repository --repository-name mds06-mlflow-tools --region eu-central-1 || true
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin 832828869208.dkr.ecr.eu-central-1.amazonaws.com

# З КОРЕНЯ репозиторію: контекст мусить бачити і train/, і drift/ —
# у контрактному образі лежать ОБА скрипти.
docker buildx build --platform linux/amd64 -f train/Dockerfile --push \
  -t 832828869208.dkr.ecr.eu-central-1.amazonaws.com/mds06-mlflow-tools:v1 .
```

`--platform linux/amd64` **обовʼязково**: Mac ARM, ноди x86_64. Забули —
`exec format error`. Збірка 8-12 хв через QEMU-емуляцію.

Один образ на дві задачі: `command: ["python", "train.py"]` для Job
тренування і `command: ["python", "drift_exporter.py"]` для Deployment
експортера. Є ще необовʼязковий `drift/Dockerfile` — той самий експортер без
mlflow/boto3/pandas/matplotlib, ~330 MiB замість ~1.1 GiB. Сенс: под експортера
працює 24/7, а MLflow йому не потрібен ні на секунду. Контрактний образ — усе
одно `train/Dockerfile`.

### 5. ArgoCD: чотири Application

```bash
kubectl apply --server-side=true -f argocd/app-minio.yaml
kubectl apply --server-side=true -f argocd/app-postgres.yaml
kubectl apply --server-side=true -f argocd/app-mlflow.yaml
kubectl apply --server-side=true -f argocd/app-drift.yaml
kubectl -n argocd get applications -w
```

`--server-side=true` обовʼязковий: client-side apply запихає весь маніфест в
анотацію `last-applied-configuration` і падає з `metadata.annotations: Too long:
may not be more than 262144 bytes`.

| Хвиля | Application | Що робить |
|---|---|---|
| 0 | `minio` | standalone, PVC 8Gi gp3; Healthy лише після PostSync-хука, який створює bucket `mlflow-artifacts` |
| 0 | `postgres` | PostgreSQL 18.6, PVC 8Gi gp3 |
| 1 | *(вільна)* | зарезервована під `ExternalSecret`, якщо переїжджатимете на External Secrets Operator (слайд 23) |
| 2 | `mlflow` | tracking server + UI |
| 3 | `drift-exporter` | Deployment + Service + ServiceMonitor з теки `k8s/` (через kustomize) |

> **🔴 Чому хвилі тут — документація, а не механізм.** Анотація
> `argocd.argoproj.io/sync-wave` діє в межах **одного** синку. Ці чотири
> Application подані окремими `kubectl apply`, тож ArgoCD синкає їх незалежно і
> черговість **не гарантована**. Щоб хвилі справді працювали, потрібен
> **app-of-apps**: один батьківський Application, що дивиться на теку
> `argocd/` — у ArgoCD є вбудований health-check для `kind: Application`
> (Healthy лише коли дочірній Synced+Healthy), тому батько реально ЧЕКАЄ.
> Це ж лікує граблі №5 Теми 8: **зараз правка `valuesObject` у Git нічого не
> змінює**, доки не зробиш `kubectl apply` повторно. Свідомо не зроблено —
> гарне домашнє завдання, а не прихований борг.
>
> **Коректність від хвиль не залежить**, і це не самовиправдання, а два
> реальних пояси. У MLflow `databaseConnectionCheck: true` додає
> init-контейнер `dbchecker`, який чекає `PGHOST:PGPORT` з фібоначчі-бекофом, а
> `databaseMigration: true` виносить міграції в окремий init-контейнер. У MinIO
> PostSync-хук не віддає Healthy, поки bucket не створено. Тобто навіть при
> одночасному застосуванні MLflow не впаде — почекає в `Init`.

### 5б. Дашборд Grafana — окремим apply

```bash
kubectl apply -f grafana/dashboard-configmap.yaml
```

Чому не через ArgoCD разом з експортером: `k8s/kustomization.yaml` має
`namespace: mlflow` і **перезаписав би** `namespace: monitoring` цього
ConfigMap. Технічно сайдкар Grafana знайшов би його й у `mlflow`
(`searchNamespace: ALL`), тож якщо хочете все під GitOps — додайте файл у
`resources:` кустомізації і змиріться, що обʼєкт житиме в чужому namespace.
Ліниве й чесне рішення — один `kubectl apply`, бо дашборд змінюється раз на
тему, а не раз на комміт.

### 6. 🚦 Перевірити, що метрики реально є

```bash
kubectl -n mlflow get servicemonitor          # мусить бути 2: mlflow і drift-exporter
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090
# у /targets шукати: mlflow (path /mlflow/metrics!), drift-exporter (path /metrics)
```

Це найтихіша пастка теми: ArgoCD пише Synced/Healthy, а метрик немає.

### 7. Виправити генератор Теми 8 — без цього демо провалиться

**Заміряно:** генератор Теми 8 шле вигадані центроїди з `sigma=0.25/0.9`, і це
не розподіл справжнього Iris. KS по `petal_width` на **чистих** даних дає
p = 0.041 / 0.015 / 0.028 (вікна 600 / 3000 / 9000) → `drift_detected{feature="petal_width"} = 1`
**ще до будь-якої симуляції**. Панель червона з початку, і розповідь «дивіться,
0 стрибає в 1» розсипається.

**Виправлення — 4 рядки: семплити РЕАЛЬНІ рядки Iris** (той самий train-split,
що є еталоном) з джитером 0.05.

```python
_data = load_iris()
_X, _, _y, _ = train_test_split(_data.data, _data.target,
    test_size=0.2, random_state=42, stratify=_data.target)
ROWS = _X.tolist()

def payload():
    r = random.random()
    if r < 0.05:                      # 5% битих → 422, панель помилок не нульова
        return {"sepal_length": 999, "sepal_width": "abc",
                "petal_length": None, "petal_width": -1}
    row = random.choice(ROWS)
    return {f: round(max(0.1, random.gauss(v + DRIFT_SHIFT, JITTER)), 2)
            for f, v in zip(FIELDS, row)}
```

`JITTER=0.05` теж заміряно: при 0.10 сам джитер перекидає прикордонні зразки
через межі класів, і хі-квадрат бачить «дріфт» на чистих даних у 6% вікон.

**Перезбірка образу НЕ потрібна.** Скрипт генератора лежить у ConfigMap
`loadgen-script` (файл `k8s/loadgen.yaml` Теми 8), а не в образі, і `sklearn`
в образі `mds06-ml-model:v1` уже є:

```bash
cd /Users/alexandervasileyko/Repos/goit/mds06-ml-monitoring
$EDITOR k8s/loadgen.yaml                                    # правимо ConfigMap
kubectl apply -f k8s/loadgen.yaml
kubectl -n ml-demo rollout restart deploy/load-generator    # ConfigMap не перечитується сам
kubectl -n ml-demo logs deploy/load-generator | head -3
```

Не копіюйте масив Iris руками: скопійований масив розʼїдеться з еталоном
експортера від першої ж правки.

---

## Демонстрація дріфту на занятті

Ручка — **env-змінна в генераторі**, не новий ендпоїнт у моделі. Дріфт за
визначенням приходить ЗЗОВНІ, тож і ручка має бути в трафіку, а не в сервісі,
який цей трафік обслуговує. Хронометраж заміряний під `CHECK_INTERVAL=60`,
`WINDOW_MINUTES=10`.

| Хв | Команда / що показувати |
|---|---|
| 0 | `kubectl -n ml-demo scale deploy/load-generator --replicas=1` — норма. Усі `drift_detected` = 0, p-value «гуляють» у 0.1-0.9. **Сказати вголос: p-value ШУМИТЬ, і це нормально** — під H0 воно рівномірно розподілене |
| 3 | `kubectl -n ml-demo set env deploy/load-generator DRIFT_SHIFT=0.8` (под перестворюється ~10 с) |
| 4-5 | p падає: спершу `petal_width` і `sepal_width`, далі всі чотири. Панель 6 червоніє рядками. Стек класів «зʼїдає» setosa |
| ~14 | вікно 10 хв повністю зі зсунутих даних: p ≈ 1e-11 і нижче, усі 5 рядів червоні |
| — | `set env DRIFT_SHIFT=0` → **дашборд НЕ зеленіє одразу**: вікно 10 хв усе ще містить старі дані. Найкращий момент уроку — студент фізично бачить, що таке вікно й лаг виявлення |
| бонус | `scale deploy/load-generator --replicas=0` → `current_window_size` падає нижче 30, p-value **ЗАМИРАЮТЬ** на останніх значеннях і не скидаються в 0. Пояснити, чому дашборд, який зеленіє від відсутності даних, небезпечний, і навіщо панель 3 (свіжість) |

Заміри, на яких це тримається:

| Режим | Вікон | KS по 4 ознаках | Хі-квадрат класів |
|---|---|---|---|
| норма (`DRIFT_SHIFT=0`) | 200 × 3000 записів | 0/200 хибних, мін. p = 0.295 | 4/200 (2%) при порозі 0.01; 12/200 (6%) при 0.05 |
| дріфт (`DRIFT_SHIFT=0.8`) | 20 | найгірший p = 2.48e-11 | p → 0.0 |

Частки класів під дріфтом (справжня модель): setosa 0.335 → 0.022,
virginica 0.353 → 0.626.

---

## Дашборд

`grafana/drift-dashboard.json`, `uid=model-quality-drift`, 13 панелей.

| # | Панель | PromQL |
|---|---|---|
| 1 | Дріфт зараз: скільки ознак | `sum(drift_detected) or vector(0)` |
| 2 | Найпроблемніша ознака | `bottomk(1, drift_p_value)` |
| 3 | Свіжість перевірки | `time() - drift_check_timestamp_seconds` |
| 4 | Розмір вікна | `current_window_size` |
| 5 | Тест взагалі виконується? | `current_window_size >= bool 30` |
| 6 | Дріфт по ознаках у часі (state timeline) | `drift_detected` |
| 7 | p-value, лог-шкала + 2 лінії порогу | `drift_p_value`, `vector(0.01)`, `vector(0.05)` |
| 8 | Стан ознак зараз (table, instant) | `sort(drift_p_value)` |
| 9 | Розподіл класів у прогнозах (stacked) | `prediction_class_share` |
| 10 | Відхилення частки класу від еталона | `prediction_class_share - scalar(1 / count(prediction_class_share))` |
| 11 | Поточне вікно проти еталона | `current_window_size`, `reference_dataset_size` |
| 12 | Затримка p95 (слайд 34) | `histogram_quantile(0.95, sum by (le) (rate(predict_latency_seconds_bucket[$__rate_interval])))` |
| 13 | Частка помилок і трафік (слайд 34) | `sum(rate(http_requests_total{status=~"4..\|5.."}[..])) / sum(rate(http_requests_total[..]))` |

**Тонкощі, які легко зіпсувати:**

- **Поле `interval` панелі приймає ЛІТЕРАЛ.** У панелі 6 стоїть `"1m"`.
  `$__rate_interval` у цьому полі ламає панель цілком —
  `Invalid interval string`. Змінна валідна **тільки всередині виразу**
  (панелі 12-13). На цьому вже спіткнулись у Темі 8.
- **Логарифмічна шкала на панелі 7 не для краси:** норма 0.1-0.9, дріфт 1e-11.
  На лінійній шкалі момент спрацювання виглядав би як вертикальна лінія в нуль.
- **Хі-квадрат при сильному дріфті віддає рівно `0.0`**, а нуль на лог-шкалі
  не малюється. Зникла лінія = найсильніший сигнал, а не пропуск даних.
- **`or vector(0)` у панелі 1** — доки експортер не зробив жодної перевірки,
  метрики не існує, і «No data» виглядало б як «дріфту немає».
- **`scalar()` у панелі 10** обовʼязковий: без нього PromQL шукає збіг лейблів
  між частинами виразу і повертає порожньо.
- **`bool` у панелі 5**: без модифікатора порівняння працює як фільтр і прибирає
  ряд — панель показала б «No data» замість «ні».

Дашборд читає рівно ці метрики й нічого більше:

| Метрика | Тип | Лейбли | Значення |
|---|---|---|---|
| `drift_detected` | Gauge | `feature` | 0 / 1 |
| `drift_p_value` | Gauge | `feature` | 0..1 |
| `drift_check_timestamp_seconds` | Gauge | — | unix-час останньої перевірки |
| `prediction_class_share` | Gauge | `class` | частка 0..1 |
| `reference_dataset_size` | Gauge | — | 120 |
| `current_window_size` | Gauge | — | записів у вікні |

Лейбл `feature` приймає 5 значень: `sepal_length`, `sepal_width`,
`petal_length`, `petal_width` (KS-тест) і `prediction` (хі-квадрат по класах).
Метрики Теми 8, які теж використані: `predict_latency_seconds_bucket`,
`http_requests_total`, `predict_requests_total`.

### Алерти (у дашборді їх немає — це домашнє завдання)

```promql
# СТІЙКИЙ ДРІФТ — min_over_time відсіює виміряні 2% хибних спрацювань,
# бо вимагає дріфту в КОЖНІЙ перевірці за 10 хвилин
min_over_time(drift_detected[10m]) == 1

# ЕКСПОРТЕР МЕРТВИЙ — без цього дашборд показує застарілі зелені Gauge і бреше
time() - drift_check_timestamp_seconds > 300

# ДАНІ ЗАКІНЧИЛИСЬ — тиша ≠ норма
current_window_size < 30
```

---

## ⚠️ Розбіжності з презентацією

Проговоріть їх студентам явно — вони наткнуться.

| Слайд | Що каже | Як насправді |
|---|---|---|
| 35 | «Evidently AI аналізує дані → **віддає статистику в Prometheus** → Grafana малює дашборд» | **Цієї стрілки не існує в жодній версії Evidently.** Модуль `evidently.model_monitoring`, на якому побудовані ВСІ статті «Evidently + Prometheus + Grafana», **вилучено** (його немає вже у 0.6.5). Стрілку пише інженер: цикл опитування + `prometheus_client.Gauge` + `/metrics`. Наш `drift_exporter.py` — рівно вона, лише зі `scipy` у ролі «аналізує» |
| 35 | Evidently як основний інструмент дріфту | API 0.7.21 змінився ПОВНІСТЮ: немає ні `from evidently.report import Report`, ні `metric_preset`, ні `ColumnMapping`, ні `as_dict()`. Туторіали 0.4.x не запустяться. Плюс `run(current, reference)` — порядок аргументів **позиційний і зворотний до звички**. У гарячому шляху ми його не тримаємо (+500-700 MiB: litestar, uvicorn, plotly, statsmodels, nltk, pyarrow), але лишили **окремим лабораторним кроком** — інтерактивний HTML-звіт як артефакт MLflow (вправа 4б) |
| 36, 41-43 | Seldon Alibi Detect: KS, chi-squared, autoencoder | **Технічно неможливий у цьому образі, а не «незручний».** `setup.py` прибиває `numpy>=1.16.2,<2.0.0`, а модель Теми 8 стоїть на `numpy==2.5.2` і `mlflow 3.15.1` мігрував на numpy 2. Один контрактний образ не може містити і MLflow, і alibi-detect. Плюс `numba<0.60` без wheel під Python 3.13, і ~1 GiB ваги. KS і хі-квадрат зі слайдів 41-43 ми реалізували на `scipy.stats` — 40 рядків, які студент читає повністю. Alibi Detect згадуємо як «наступний рівень» там, де він реально виграє: MMD, autoencoder на зображеннях і ембедінгах |
| 22 | PostgreSQL як backend store | Так, але **не bitnami**. З 28.08.2025 версійні теги перенесено в `docker.io/bitnamilegacy` (заморожені), безкоштовно лишився лише мутабельний `latest` — саме тому в `bitnami/postgresql 18.8.6` стоїть `image.tag: latest`. Перевірено `curl`: `charts.bitnami.com/bitnami/postgresql-18.8.6.tgz` → **HTTP 403**. А `community-charts/mlflow` тягне цю залежність у своєму `Chart.yaml`, тому `postgresql.enabled: false` і `mysql.enabled: false` — **не опція, а вимога**. Беремо `groundhog2k/postgres` 1.6.8 з офіційним `docker.io/postgres:18.6` |
| 21 | MinIO — розгортаємо в кластері | Так, чарт `minio-official/minio` 5.4.0. Але: **дефолт `resources.requests.memory` = 16Gi** (розрахований на bare-metal) при 3.2 GiB allocatable — под був би Pending НАЗАВЖДИ. І дефолт — `mode: distributed` з `replicas: 16`. Плюс `image.tag` **не підіймати**: чарт пінить `RELEASE.2024-12-18`, де вебконсоль ще ПОВНА, а після ~2025-04 адмін-функції вирізано в платний AIStor (лишився object browser). «Застарілий» чарт тут — перевага |
| 24 | MLflow як Helm-чарт community-charts | Працює (1.11.4, appVersion 3.15.1). Але `serviceMonitor` чарта загорнутий у `{{ if .Capabilities.APIVersions.Has "monitoring.coreos.com/v1" }}`, а ArgoCD repo-server рендерить `helm template` **без доступу до кластера** → умова завжди false і ServiceMonitor не зʼявляється **взагалі**. Лікується `apiVersions: ["monitoring.coreos.com/v1", "monitoring.coreos.com/v1/ServiceMonitor"]` у `spec.source.helm` (двома рядками — Helm звіряє точний рядок). І `telemetryPath: /mlflow/metrics`, бо чарт віддає метрики не на `/metrics` |
| 17 | «розгорнемо декларативно через ArgoCD» | Чарти й values — так, повністю. Але **самі Application НЕ під GitOps**: їх створює `kubectl apply`, тому правка `valuesObject` у Git не доїжджає в кластер без повторного apply (граблі №5 Теми 8, тут вони НЕ вилікувані). І як наслідок — `sync-wave` між цими Application є документацією, а не механізмом: анотація діє в межах одного синку. Лікується патерном **app-of-apps**; свідомо лишено як домашнє завдання |
| — | (немає слайда) | **StorageClass `gp2` непрацездатний** — див. перший розділ. Це головна робота теми, і в презентації її немає зовсім |

---

## Доступ

```bash
kubectl -n mlflow      port-forward svc/mlflow 5000:80          # MLflow UI
kubectl -n mlflow      port-forward svc/minio-console 9001:9001 # MinIO UI (логін з Secret)
kubectl -n monitoring  port-forward svc/monitoring-grafana 3000:80   # admin/admin
kubectl -n monitoring  port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090
kubectl -n logging     port-forward svc/loki 3100:3100
kubectl -n mlflow      port-forward deploy/drift-exporter 9100:9100  # сирі метрики

# креденшели MinIO
kubectl -n mlflow get secret mlflow-credentials -o jsonpath='{.data.rootUser}' | base64 -d; echo
kubectl -n mlflow get secret mlflow-credentials -o jsonpath='{.data.rootPassword}' | base64 -d; echo
```

Або `ports.sh` із репозиторію Теми 8 — він піднімає все разом.

---

## Пастки, які варто знати ДО того, як наткнетесь

Кожна знайдена на етапі підготовки, з причиною і лікуванням.

| Симптом | Причина | Лікування |
|---|---|---|
| Под `minio` назавжди Pending, `Insufficient memory` | дефолт `requests.memory: 16Gi` у чарті | `mode: standalone`, `replicas: 1`, `requests.memory: 512Mi`. **Правило: читати дефолтні requests чужого чарта ПЕРЕД деплоєм** — `helm show values ... \| grep -A3 resources` |
| Після редеплою MinIO новий под вічно `ContainerCreating`, старий `Terminating`, `Multi-Attach error` | standalone MinIO — це Deployment, а дефолт `RollingUpdate {maxUnavailable: 0}` підіймає новий под, поки старий тримає RWO-том EBS. Взаємне блокування | `deploymentUpdate: {type: Recreate}`. Стосується **будь-якого** Deployment із RWO PVC |
| Под `mlflow` у `Init:CrashLoopBackOff`, у логах нічого. ArgoCD показує Progressing, не Degraded | init-контейнер `mlflow-db-migration` успадковує `.Values.resources`; при ліміті ~256Mi його тихо вбиває OOMKilled | `limits.memory` не нижче **768Mi**. Дивитись саме init: `kubectl -n mlflow logs <pod> -c mlflow-db-migration` |
| ServiceMonitor існує, таргет UP, метрик нема (або 404) | чарт віддає `--expose-prometheus=/mlflow/metrics`, а `telemetryPath` дефолтом `/metrics`; `serviceMonitor.namespace` дефолтом `monitoring`; `labels.release` дефолтом `prometheus` | `telemetryPath: /mlflow/metrics`, `namespace: mlflow`, `labels: {release: monitoring}` |
| Ротували `POSTGRES_PASSWORD`, перезапустили под → `password authentication failed for user "mlflow"` | офіційний образ postgres виконує `docker-entrypoint-initdb.d` **тільки на порожньому PGDATA**. Пароль записаний у файли БД при першому старті; зміна Secret на вже проініціалізований том не впливає ніяк | `ALTER USER mlflow PASSWORD '...'` у самій БД, або знести PVC разом з даними. Це готова вправа (див. 8г) |
| `log_artifact` / `log_model` падає з `EndpointConnectionError` на `s3.amazonaws.com` або `NoSuchBucket` | при `proxiedArtifactStorage: false` артефакти вантажить **КЛІЄНТ** напряму в MinIO. `MLFLOW_S3_ENDPOINT_URL` на СЕРВЕРІ не допомагає. Чарт підставляє цю змінну автоматично лише коли `minio.enabled=true` (сабчарт) — у нас MinIO окремий Application | клієнту `envFrom: [{secretRef: {name: mlflow-credentials}}]`. Або `proxiedArtifactStorage: true` — тоді клієнту потрібен ЛИШЕ `MLFLOW_TRACKING_URI`, ціною всього трафіку артефактів через один под |
| Под `postgres`/`minio` назавжди Pending: `node(s) had volume node affinity conflict` | EBS-том живе в ОДНІЙ AZ і прибитий до неї назавжди | `volumeBindingMode: WaitForFirstConsumer` знімає проблему при СТВОРЕННІ тому, але **не при переїзді**. Тримати ≥2 ноди й не зсаджувати до 1 |
| Secret `mlflow-flask-server-secret-key` змінюється на кожному синку | шаблон згенерований як `{{ if not (lookup ...) }}`, а `lookup` під `helm template` завжди повертає порожньо | **ІГНОРУВАТИ**: `auth.enabled: false`, SECRET_KEY нікуди не використовується |
| Дашборд у Grafana не зʼявився | сайдкар `grafana-sc-dashboard` мертвий (OOM) або мітка не рівно `"1"` | `kubectl get pod -n monitoring -l app.kubernetes.io/name=grafana` і дивитись **READY**, не STATUS |

---

## Прибирання

Порядок критичний. **`terraform destroy` НЕ видаляє PV**: їх створив драйвер,
Terraform про них не знає, а в модуля EKS `preserve = true` за замовчуванням.

```bash
kubectl delete -f argocd/                                # спершу Application:
                                                         # finalizer прибере їхні ресурси
kubectl delete -n monitoring configmap drift-dashboard
kubectl delete pvc --all -n mlflow                       # ОБОВʼЯЗКОВО: у postgres
                                                         # whenDeleted: Retain, сам не зникне
aws ec2 describe-volumes --filters Name=status,Values=available \
  --region eu-central-1 --query 'Volumes[].VolumeId'      # мусить бути []
kubectl delete ns mlflow --ignore-not-found
aws ecr delete-repository --repository-name mds06-mlflow-tools --region eu-central-1 --force
cd terraform && terraform destroy
```

Забутий 8 GiB gp3 тихо тягне **$0.76/міс роками**. Це і є справжня фінансова
міна теми — не тариф, а осиротілі томи.

---

## Скільки це коштує

Франкфурт, gp3 = $0.0952 за GiB-місяць (на 20% дешевше за gp2 $0.119 — саме
тому gp3). Базові 3000 IOPS і 125 MB/s входять у ціну.

| Обсяг | Місяць | Доба | Година |
|---|---|---|---|
| 8 GiB | $0.76 | $0.025 | $0.001 |
| **16 GiB (MinIO 8 + Postgres 8)** | **$1.52** | **$0.050** | **$0.002** |

Контекст, який міняє сприйняття: EKS control plane $2.40/добу, 2 × t3.medium
$2.30/добу, NAT Gateway $1.25/добу. Базова ціна кластера ≈ **$5.95/добу**, і
16 GiB EBS додають до неї **0.8%**. Третя нода коштує $1.09/добу — тобто в
**22 рази дорожче за все сховище**. Сховище тут найдешевша частина; дорого
коштує сам факт, що кластер увімкнений.

---

## Структура репозиторію

```
argocd/
  app-minio.yaml         wave 0   minio-official/minio 5.4.0
  app-postgres.yaml      wave 0   groundhog2k/postgres 1.6.8
  app-mlflow.yaml        wave 2   community-charts/mlflow 1.11.4
  app-drift.yaml         wave 3   дивиться на теку k8s/ (kustomize)
  README.md              порядок застосування і що свідомо не зроблено
k8s/
  namespace.yaml
  drift-exporter.yaml    Deployment + Service + ServiceMonitor
  secret-example.yaml    ШАБЛОН: 11 ключів із заглушками + команда створення.
                         Навмисно НЕ в kustomization — інакше ArgoCD затер би
                         реальні паролі рядком «ЗАМІНІТЬ...»
  kustomization.yaml     namespace: mlflow + тег образу в одному місці
train/
  train.py        сітка 3×2 = 6 запусків, log_param/metric/artifact/model,
                         реєстрація найкращого як iris-rf@champion
  requirements.txt       піни: mlflow 3.15.1, boto3, sklearn 1.9.0, numpy 2.5.2,
                         scipy 1.18.0, pandas, matplotlib, prometheus-client
  Dockerfile             КОНТРАКТНИЙ образ mds06-mlflow-tools:v1 (обидва скрипти)
drift/
  drift_exporter.py      Loki → ks_2samp / chisquare → Prometheus
  test_drift.py          самоперевірка логіки (лежить і в образі)
  requirements.txt       строга підмножина train/
  Dockerfile             необовʼязковий легкий образ, ~330 MiB
grafana/
  drift-dashboard.json   13 панелей
  dashboard-configmap.yaml
EXERCISES.md             8 вправ для студентів
```

Самоперевірку експортера можна прогнати прямо в кластері, не відтворюючи venv:

```bash
kubectl -n mlflow exec deploy/drift-exporter -- python test_drift.py
```

Сховище живе в іншому репозиторії, бо це Terraform:
`mds06-kuber-from-scratch/terraform/ebs-csi-iam.tf` і
`mds06-kuber-from-scratch/deploy/0-storage/storageclass-gp3.yaml`.

---

## Версії

MLflow 3.15.1 (чарт community-charts/mlflow 1.11.4) · PostgreSQL 18.6 (чарт
groundhog2k/postgres 1.6.8) · MinIO RELEASE.2024-12-18 (чарт minio 5.4.0) ·
ArgoCD 3.5.0 · Kubernetes 1.34 · EBS CSI driver (EKS addon) · scipy 1.18.0 ·
scikit-learn 1.9.0 · numpy 2.5.2 · Evidently 0.7.21 (лабораторний крок) ·
Grafana 13.1.3 · Python 3.13

Наступна тема — GitLab CI + AWS Step Functions (слайд 46).
