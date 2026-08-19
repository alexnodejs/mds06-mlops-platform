# Моніторинг ML-моделі в Kubernetes

**Тема 8. Моніторинг та логування ML-моделей** — практична частина
Курс MLOps CI/CD 2.0

ML-модель (Iris / RandomForest) на FastAPI, обкладена повним стеком
спостережуваності: **Prometheus + Grafana + Loki**, усе розгорнуте через
**ArgoCD** за GitOps-підходом (слайд 22).

> **Прогнано на живому EKS-кластері.** Усі дочірні ArgoCD Application із
> `argocd/apps/` (їх 10 на весь курс, із них 5 — про цю тему) — `Synced/Healthy`.
> Дашборд віддає дані: 4.11 передбачень/с. Логи в Loki знаходяться запитом.
> Нижче — реальні дефекти, які виявило саме розгортання.

---

## Що піднімається

| Namespace | Компонент | Подів |
|---|---|---|
| `monitoring` | Prometheus, Grafana, kube-state-metrics, operator + node-exporter (DaemonSet) | 4 + по одному на ноду |
| `logging` | Loki (SingleBinary) + Alloy, збирач логів (DaemonSet) | 1 + по одному на ноду |
| `ml-demo` | ml-model ×2, load-generator | 3 |

Два з цих компонентів — DaemonSet, тож їхня кількість росте разом із нодами:
на дефолтних **3 нодах** (`node_desired_size` у `terraform/cluster/variables.tf`)
тема додає 14 подів. Бюджет слотів рахований там же: t3.medium вміщає ~17 подів,
3 ноди = 51 слот, і повний стек курсу (Теми 6, 8, 9) у нього влазить із запасом.
На 2 нодах — рівно 34 з 34, і транзієнтний Job тренування вже не отримує слота.

```
   ml-model (FastAPI)                    Prometheus ──┐
   ├── /predict   інференс               (pull /metrics)
   ├── /metrics   ← скрейпить Prometheus              ├──► Grafana
   └── stdout     ← читає Alloy → Loki    Loki ───────┘     (дашборд)
```

---

## Швидкий старт

```bash
# 0. ПЕРЕДУМОВИ: кластер із Теми 5 і ArgoCD із Теми 6. Обидві перевіряє make up
#    і зупиняється з поясненням, якщо чогось немає.
kubectl get ns argocd     # немає -> docs/06-deploy-methods.md
kubectl get sc gp3        # немає -> kubectl apply -f deploy/0-storage/storageclass-gp3.yaml

# 1. Образи в ECR. --platform linux/amd64 зашито в scripts/build-images.sh:
#    мак розробника ARM, ноди EKS x86_64, без цього под падає з exec format error.
make images               # mds06-ml-model:v5 + mds06-mlflow-tools:v2 + mds06-react-gitops:v2

# 2. Увесь стек однією командою
make up
```

`make up` (це `scripts/up.sh`) робить рівно п'ять речей:

1. перевіряє передумови — namespace `argocd`, StorageClass `gp3`, кількість нод;
2. створює Secret `mlflow-credentials` у `mlflow` і `ml-demo` — паролі генеруються
   один раз у `~/.mlflow-demo-credentials` (chmod 600), бо в Git їм не місце;
3. `kubectl apply -f argocd/root.yaml` — **одна** команда на весь стек, далі
   ArgoCD сам розгортає дочірні Application із `argocd/apps/`;
4. вмикає генератор трафіку (`kubectl -n ml-demo scale deploy/load-generator
   --replicas=1`, те саме окремо — `make loadgen`) — без нього графіки порожні;
5. проганяє `scripts/train.sh`, щоб MLflow Теми 9 не був порожній, і насамкінець
   друкує таблицю сервісів (`make ports`).

Ручного очікування CRD Prometheus **більше немає**: порядок задають анотації
`argocd.argoproj.io/sync-wave` у файлах `argocd/apps/` — ArgoCD не почне хвилю
N+1, доки хвиля N не стане Healthy. Моніторинг — хвиля 0, Loki і Alloy — 1,
модель із дашбордом — 2.

Перший `make up` — **5-10 хвилин**, із них ~3 хв ставляться CRD Prometheus.
Скрипт чекає до 10 хв, потім показує `kubectl get application -n argocd`, щоб
було видно, хто саме не піднявся.

---

## 🔴 Дефекти, які знайшло тільки живе розгортання

Це найцінніша частина документа. Кожен пункт реально стався.

### 1. Сайдкари Grafana падали з OOMKilled — і ніхто цього не бачив

**Симптом, що збиває з пантелику:** ArgoCD показує `Synced/Healthy`, под
Grafana — `Running`. А дашбордів немає **жодного**, і datasource теж жодного.

```bash
kubectl get pod -n monitoring -l app.kubernetes.io/name=grafana
# READY 1/3   ← ось воно. STATUS каже Running, але готовий 1 контейнер із 3
```

**Причина:** `limits.memory: 64Mi` для сайдкарів. `k8s-sidecar` — це Python
із `kubernetes-client`, який тримає WATCH на ConfigMap у **всіх** namespace.
Сам інтерпретатор з'їдає 70-90 MiB ще до першого об'єкта. Exit code 137.

**Урок для студентів:** `Healthy` в ArgoCD і `Running` у kubectl —
**не означають, що працює**. Перевіряйте колонку `READY`.

### 2. Дубльований ключ у YAML зникає мовчки

Під `grafana.sidecar` опинилось два ключі `datasources:`. YAML залишає
**останній**, перший зникає без жодного попередження. Налаштування
`alertmanager.enabled: false` просто не діяло.

**Урок:** після кожної правки values — `python3 -c "import yaml; print(yaml.safe_load(open(f)))"`
і дивіться, що реально вийшло.

### 3. Application-и самі не були під GitOps — виправлено патерном app-of-apps

**Як було** (файли тоді лежали просто в `argocd/`, без підтеки `apps/`). Кожен
Application застосовувався руками: `kubectl apply -f argocd/app-monitoring.yaml`,
і так дев'ять разів, з трьох різних репозиторіїв. Виходив парадокс: ArgoCD
стежить, щоб у кластері було рівно те, що в Git, але **самі Application-и
в Git ніхто не звіряв**. Правка `app-monitoring.yaml` у Git не застосовувалась
автоматично; видалення Application через `kubectl` ArgoCD спокійно приймав.

```bash
kubectl get application monitoring -n argocd -o jsonpath='{.status.sync.revision}'
# 88.3.0  ← це версія ЧАРТУ, а не коміт Git
```

Це і досі так: у `monitoring` джерело — Helm-репозиторій, тож `revision`
показує версію чарта. Дірка була не в цьому, а в тому, що **сам файл
Application** жив поза GitOps.

**Як стало.** У репозиторії є `argocd/root.yaml` — кореневий Application, який
стежить за текою `argocd/apps/` і застосовує решту. Рекурсії немає: root лежить
у `argocd/`, а дивиться в `argocd/apps/`.

```bash
kubectl apply -f argocd/root.yaml            # одна команда замість десяти
kubectl get application -n argocd            # root + 10 дочірніх
```

Що це дало конкретно:

- `prune: true` — прибрали файл із `argocd/apps/` у Git, і Application зникає з кластера;
- `selfHeal: true` — видалили Application через `kubectl`, і ArgoCD повертає його;
- `resources-finalizer.argocd.argoproj.io` на root — `kubectl delete -f
  argocd/root.yaml` каскадом зносить дочірні Application, а їхні фіналайзери —
  усе, що ті створили. Саме на цьому побудований `make down`;
- порядок задають анотації `sync-wave` у дочірніх файлах, тому ручне
  `kubectl wait` на CRD Prometheus зі старої інструкції більше не потрібне.

**Що GitOps так і не покриває:** Secret `mlflow-credentials`. У ньому паролі,
у Git їм не місце, тож його створює `make up` поза Git. Без нього Application-и
`minio`, `postgres` і `mlflow` застрягають у Progressing — і це виглядає як
поламаний ArgoCD, хоча ArgoCD ні до чого.

### 4. Гістограма міряла не те, що написано на панелі

Спочатку таймер стояв навколо `predict_proba` і давав **1.46 мс**, тоді як
реальна відповідь — **10.5 мс**. Панель зветься «Час відповіді», студент
побачив би цифру в 7 разів меншу за правду.

Виправлено: замір перенесено в middleware, і тепер він охоплює валідацію
pydantic, серіалізацію та накладні витрати HTTP. Час самого інференсу
лишився в логах під іншим іменем — `inference_ms`, щоб цифри не суперечили.

### 5. Prometheus рахував власні скрейпи

`http_requests_total` включав походи Prometheus на `/metrics` — раз на 60 с
до кожної репліки. `sum(rate(http_requests_total[5m]))` показував трафік
моніторингу і видавав його за навантаження сервісу. Виключено в middleware.

---

## ⚠️ Розбіжності з презентацією

Проговоріть їх студентам явно — вони наткнуться.

| Слайд | Що каже | Як насправді |
|---|---|---|
| 29 | чарт `grafana/loki-stack` | **DEPRECATED**. Використано `grafana/loki` 7.3.0 (SingleBinary) + Grafana Alloy замість Promtail |
| 37 | `avg(response_time_seconds)` | Такої метрики не існує. `prometheus_client` дає гістограму → `rate(..._sum[5m]) / rate(..._count[5m])` |
| — | — | **StorageClass `gp2`, який EKS створює сам, непрацездатний**: провайдер `kubernetes.io/aws-ebs` вилучено в K8s 1.31. Будь-який PVC на ньому зависає в `Pending` назавжди — без `ProvisioningFailed`, просто тиша в Events. Робочий клас — `gp3` (`deploy/0-storage/storageclass-gp3.yaml`, ставить `make cluster-up`) поверх addon `aws-ebs-csi-driver` |

Персистентність у кластері є (Тема 9 тримає на `gp3` MinIO і Postgres), але
**моніторинг свідомо без неї**: у `argocd/apps/app-monitoring.yaml` Prometheus
має `storageSpec.emptyDir` з `sizeLimit: 2Gi` і `retention: 12h`, Grafana —
`persistence.enabled: false`. Це економія слотів і грошей на навчальному
кластері, а не наслідок зламаного сховища.

Наслідок: **перезапуск пода Prometheus стирає метрики**, пода Loki — логи.
Для заняття прийнятно; у проді сюди йде PVC на `gp3`.

---

## Дашборд (слайд 37)

`k8s/grafana-dashboards/ml-model-dashboard.json`, `uid=ml-model-monitoring`,
7 панелей. Усі перевірені на живих даних.

| # | Панель | PromQL |
|---|---|---|
| 1 | Кількість запитів | `sum(rate(predict_requests_total{namespace="ml-demo"}[$__rate_interval]))` |
| 2 | Час відповіді + p95 | `rate(..._sum[..]) / rate(..._count[..])` та `histogram_quantile(0.95, ...)` |
| 3 | Помилки 4xx/5xx | `sum by (status) (rate(http_requests_total{status=~"4..\|5.."}[..]))` |
| 4 | Розподіл класів | `sum by (predicted_class) (increase(predict_requests_total[$__range]))` |
| 5 | Confidence | `sum by (le) (increase(predict_confidence_bucket[$__rate_interval]))` |
| 6a/6b | CPU / Пам'ять | `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes` |

**Дві тонкощі, які легко зіпсувати:**

- Панель 5 має `"interval": "$__rate_interval"`. Без нього вікна `increase()`
  перекриваються ~48 разів, і в тултипі буде 1200 замість 25.
- Панелі 6a/6b фільтрують `container!="", container!="POD"`. Лише
  `container!=""` прибирає агрегат пода, але **не** pause-контейнер, і
  пам'ять завищується.

---

## Логи в Loki

```logql
{namespace="ml-demo"} | json | event="predict"          # усі передбачення
{namespace="ml-demo"} | json | event="validation_error" # биті запити з payload
{namespace="ml-demo"} | json | confidence < 0.7         # невпевнені передбачення
```

Формат логу — JSON у stdout: `ts, level, event, request_id, input,
prediction, confidence, inference_ms`.

**Чому `request_id` у тілі, а не в мітці Loki:** Loki створює окремий стрім
на кожну унікальну комбінацію міток. Унікальний ID на запит = мільйони
стрімів = Loki лягає. Мітки — тільки низькокардинальні (`namespace`, `pod`).

---

## Доступ

Усі тунелі піднімає одна команда — вона ж друкує таблицю з логінами й
позначає ✅/❌, чи сервіс справді відповідає:

```bash
make ports              # підняти тунелі + таблиця (це scripts/ports.sh)
make clean              # зупинити всі тунелі
```

| Сервіс | Порт | Логін | Тема |
|---|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `admin` | 8 |
| Prometheus | http://localhost:9090 | не потрібен | 8 |
| ML-модель | http://localhost:8000 | `POST /predict` | 8 |
| Loki | http://localhost:3100 | лише API, UI — у Grafana | 8 |
| ArgoCD | https://localhost:8080 ⚠️ **https** | `admin` / з `secret/argocd-initial-admin-secret` | 6 |
| MLflow | http://localhost:5001 | не потрібен | 9 |
| MinIO | http://localhost:9001 | `minioadmin` / з `~/.mlflow-demo-credentials` | 9 |
| Дріфт-експортер | http://localhost:9101/metrics | сирі метрики | 9 |

⚠️ **MLflow на 5001, а не 5000.** Порти 5000 і 7000 на macOS тримає AirPlay
Receiver: тунель туди мовчки не встає, а `curl` отримує 403 від AirTunes.
Це не помилка Kubernetes і не помилка MLflow.

Сервіси, які стоять за цими портами (на випадок, коли треба зробити руками):

```bash
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80
kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090
kubectl port-forward -n ml-demo    svc/ml-model 8000:80
kubectl port-forward -n logging    svc/loki 3100:3100

curl -X POST localhost:8000/predict -H 'Content-Type: application/json' \
  -d '{"sepal_length":5.1,"sepal_width":3.5,"petal_length":1.4,"petal_width":0.2}'
```

---

## Демонстрації на занятті

```bash
# Метрики й логи разом: вбити под і побачити провал на графіку + подію в Loki
kubectl delete pod -n ml-demo -l app=ml-model --force

# Зростання помилок: генератор шле 5% битих payload — панель 3 їх показує,
# а в Loki видно, ЯКИЙ саме payload прилетів
kubectl logs -n ml-demo -l app=ml-model --tail=50 | grep validation_error

# Навантаження вгору → панелі 1 і 6a реагують
kubectl -n ml-demo scale deploy/load-generator --replicas=3
```

---

## Прибирання

```bash
make down          # знести стек; кластер і ArgoCD лишаються
make cluster-down  # знести сам EKS (Тема 5) — тільки ПІСЛЯ make down
```

`make down` (це `scripts/down.sh`) видаляє **один** ресурс:

```bash
kubectl delete -f argocd/root.yaml --timeout=300s
```

Далі працює каскад фіналайзерів: root → 10 дочірніх Application → усе, що вони
створили. Списку імен у скрипті немає навмисно — раніше він був і розходився
з реальністю щоразу, коли додавали новий Application.

Після каскаду скрипт добиває те, чого ArgoCD не прибирає сам:

- сиротні Application, якщо root видалили раніше руками;
- namespace `mlflow ml-demo monitoring logging demo-react` — `CreateNamespace=true`
  їх створює, але не видаляє;
- тунелі `kubectl port-forward`.

Порядок важливий саме такий: спершу Application, потім namespace. Навпаки —
namespace зависне в `Terminating`, поки фіналайзери Application тримають ресурси.

Репозиторії ECR **не чіпайте**: `mds06-ml-model` потрібен Темі 8, а
`mds06-mlflow-tools` — Темам 9 і 10. Видаляти їх варто лише в самому кінці курсу.

## Звідки береться дашборд у кластері

Дашборд більше **не загортають у ConfigMap руками**. У
`k8s/grafana-dashboards/kustomization.yaml` стоїть `configMapGenerator`, який
читає `.json` і сам робить ConfigMap із ключем = імʼя файла — саме це імʼя
сайдкар кладе у `/tmp/dashboards`, тож для Grafana нічого не змінилось.
Мітку `grafana_dashboard: "1"` навішує той самий kustomization
(значення `"true"` або `""` **не спрацює** — ConfigMap проігнорується мовчки).

Навіщо це змінили: раніше поруч із кожним `.json` лежав рукописний
`dashboard-configmap.yaml` із тим самим JSON, продубльованим з відступом. Два
джерела правди на один дашборд: правиш `.json` — Grafana показує старе, бо
читає ConfigMap. **1263 рядки дубля прибрано**, лишились самі `.json`
(514 рядків для моделі + 1168 для дріфту Теми 9).

Суфікс-хеш в імені ConfigMap лишили увімкненим (це дефолт kustomize): правка
`.json` дає нове імʼя ConfigMap, сайдкар бачить подію і перечитує дашборд.
З `disableNameSuffixHash` імʼя не змінюється, і оновлення довелося б ловити
руками — рестартом пода Grafana посеред заняття.

## Версії

kube-prometheus-stack 88.3.0 · Grafana 13.1.3 · Loki 7.3.0 (app 3.6.12) ·
ArgoCD 3.5.0 · Kubernetes 1.34 · scikit-learn 1.9.0 · FastAPI
