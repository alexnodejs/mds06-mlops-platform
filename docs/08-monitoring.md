# Моніторинг ML-моделі в Kubernetes

**Тема 8. Моніторинг та логування ML-моделей** — практична частина
Курс MLOps CI/CD 2.0

ML-модель (Iris / RandomForest) на FastAPI, обкладена повним стеком
спостережуваності: **Prometheus + Grafana + Loki**, усе розгорнуте через
**ArgoCD** за GitOps-підходом (слайд 22).

> **Прогнано на живому EKS-кластері.** Усі 6 ArgoCD Application —
> `Synced/Healthy`. Дашборд віддає дані: 4.11 передбачень/с. Логи в Loki
> знаходяться запитом. Нижче — реальні дефекти, які виявило саме розгортання.

---

## Що піднімається

| Namespace | Компонент | Подів |
|---|---|---|
| `monitoring` | Prometheus, Grafana, kube-state-metrics, node-exporter ×2, operator | 6 |
| `logging` | Loki (SingleBinary), Alloy (збирач логів) | 2 |
| `ml-demo` | ml-model ×2, load-generator | 3 |
| | **разом додається** | **11** |

Заміряно після розгортання: **25 подів із 34**, тобто лишається запас у 9.

```
   ml-model (FastAPI)                    Prometheus ──┐
   ├── /predict   інференс               (pull /metrics)
   ├── /metrics   ← скрейпить Prometheus              ├──► Grafana
   └── stdout     ← читає Alloy → Loki    Loki ───────┘     (дашборд)
```

---

## Швидкий старт

```bash
# 0. ПЕРЕДУМОВА: кластер із Теми 5 + ArgoCD із Теми 6
kubectl get application -n argocd

# 1. Образ моделі в ECR (--platform ОБОВʼЯЗКОВО з Apple Silicon)
aws ecr create-repository --repository-name mds06-ml-model --region eu-central-1 || true
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin 832828869208.dkr.ecr.eu-central-1.amazonaws.com
cd model && docker buildx build --platform linux/amd64 \
  -t 832828869208.dkr.ecr.eu-central-1.amazonaws.com/mds06-ml-model:v1 --push . && cd ..

# 2. Хвиля 0 — моніторинг. Чекаємо на CRD, інакше ServiceMonitor не застосується.
kubectl apply -f argocd/app-monitoring.yaml
kubectl wait --for condition=established --timeout=300s \
  crd/servicemonitors.monitoring.coreos.com          # ~190 с

# 3. Хвилі 1-2 — решта
kubectl apply -f argocd/app-loki.yaml -f argocd/app-log-collector.yaml
kubectl apply -f argocd/app-ml-model.yaml -f argocd/app-dashboard.yaml

# 4. Навантаження, щоб графіки не були порожні
kubectl -n ml-demo scale deploy/load-generator --replicas=1

# 5. Дивимось
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80   # admin/admin
```

Повний час розгортання — **~8 хвилин**, з них ~3 хв займають CRD Prometheus.

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

### 3. Application-и самі не під GitOps

Правка `argocd/app-monitoring.yaml` у Git **не застосовується автоматично**.
Ці файли створює `kubectl apply` руками — їх ніхто не синхронізує з Git.

```bash
kubectl get application monitoring -n argocd -o jsonpath='{.status.sync.revision}'
# 88.3.0  ← це версія ЧАРТУ, а не коміт Git
```

Тобто в Git лежать values, а в кластері працюють ті, що були на момент
`kubectl apply`. Щоб застосувати зміну — `kubectl apply -f` ще раз.

**Як робиться правильно:** патерн **app-of-apps** — один кореневий
Application, який стежить за текою `argocd/` і застосовує решту. Тоді
Application-и теж стають частиною GitOps. Це гарна тема для наступного кроку.

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
| — | — | **StorageClass `gp2` непрацездатний**: провайдер `kubernetes.io/aws-ebs` вилучено в K8s 1.31, EBS CSI не встановлено. Будь-який PVC зависає в `Pending` назавжди. Тому весь стек — **без персистентності** |

Наслідок останнього: **перезапуск пода Prometheus стирає метрики**, пода
Loki — логи. Для заняття прийнятно; у проді потрібен EBS CSI addon.

---

## Дашборд (слайд 37)

`uid=ml-model-monitoring`, 7 панелей. Усі перевірені на живих даних.

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

```bash
kubectl port-forward -n monitoring svc/monitoring-grafana 3000:80      # admin/admin
kubectl port-forward -n argocd svc/argocd-server 8080:443              # https!
kubectl port-forward -n ml-demo svc/ml-model 8000:80
kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090

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
kubectl delete application ml-model ml-dashboard log-collector loki monitoring -n argocd
kubectl delete ns ml-demo monitoring logging --ignore-not-found
aws ecr delete-repository --repository-name mds06-ml-model --region eu-central-1 --force
```

Порядок важливий: спершу Application (finalizer прибере створене ними),
потім namespace.

## Версії

kube-prometheus-stack 88.3.0 · Grafana 13.1.3 · Loki 7.3.0 (app 3.6.12) ·
ArgoCD 3.5.0 · Kubernetes 1.34 · scikit-learn 1.9.0 · FastAPI
