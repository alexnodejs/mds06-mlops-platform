# ML-сервіс: класифікація Iris (FastAPI + Prometheus + JSON-логи)

Мінімальний ML-сервіс для теми «Моніторинг ML у Kubernetes». Модель —
`RandomForestClassifier` на вбудованому датасеті Iris; тренується **під час
збірки образу**, у рантаймі немає ні мережі, ні томів.

| Файл | Що робить |
|---|---|
| `train.py` | тренує модель, кладе `model.pkl` (модель + метадані) поруч із собою |
| `app.py` | FastAPI: `/`, `/predict`, `/healthz`, `/metrics` |
| `requirements.txt` | піни версій (перевірені на PyPI 2026-08-12) |
| `Dockerfile` | одноетапна збірка на `python:3.13-slim`, uid 1000 |

## Локальний запуск

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python train.py                 # створить model.pkl (~155 KiB)
uvicorn app:app --reload --port 8000
```

Перевірка:

```bash
curl localhost:8000/healthz
curl -X POST localhost:8000/predict -H 'Content-Type: application/json' \
  -d '{"sepal_length":5.1,"sepal_width":3.5,"petal_length":1.4,"petal_width":0.2}'
curl -s localhost:8000/metrics | grep predict_
```

Відповідь `/predict`:

```json
{"request_id":"11090354f0cc...","prediction":"setosa","confidence":1.0,
 "probabilities":{"setosa":1.0,"versicolor":0.0,"virginica":0.0},"model_version":"v1"}
```

Биті дані (`{"sepal_width":"abc"}`) → **422** від pydantic. Це навмисна частина
демо: без 4xx панель «Помилки» на дашборді була б рівною нулю всю лекцію.

## Збірка образу

Мак розробника — ARM, ноди EKS — x86_64. Без `--platform linux/amd64` под падає
з `exec format error`.

```bash
ECR=832828869208.dkr.ecr.eu-central-1.amazonaws.com
aws ecr get-login-password --region eu-central-1 \
  | docker login --username AWS --password-stdin $ECR

docker buildx build --platform linux/amd64 -t $ECR/mds06-ml-model:v1 --push .
```

Локально перевірити зібраний образ:

```bash
docker run --rm -p 8000:8000 --platform linux/amd64 $ECR/mds06-ml-model:v1
```

Розмір образу ~414 MiB на диску / ~133 MB на pull. З них 62% — ML-стек
(scipy 138 MiB, numpy 71 MiB, sklearn 48 MiB); сама модель важить 155 KiB.
`imagePullSecret` не потрібен: на нодах уже висить `AmazonEC2ContainerRegistryReadOnly`.

## Метрики

| Метрика | Тип | Лейбли |
|---|---|---|
| `predict_requests_total` | Counter | `predicted_class` |
| `predict_latency_seconds` | Histogram | — |
| `predict_confidence` | Histogram | `predicted_class` |
| `http_requests_total` | Counter | `method`, `path`, `status` |
| `model_info` | Gauge (=1) | `version`, `model_type` |

### Розбіжність із презентацією (слайд 37) — не приховуємо, пояснюємо

Слайд просить панель «Середній час відповіді» як `avg(response_time_seconds)`.
**Такої метрики не існує і існувати не може**: `prometheus_client` не має типу,
який віддавав би «середнє» окремим рядом. Правильний запит рахується з гістограми:

```promql
rate(predict_latency_seconds_sum[5m]) / rate(predict_latency_seconds_count[5m])
```

Чисельник — приріст суми секунд за секунду, знаменник — приріст кількості
запитів; ділення дає середні секунди на запит у вікні 5 хв. Саме `rate()`, а не
голе `_sum / _count`: останнє дає середнє за весь час життя пода і ніколи не
реагує на деградацію.

Так само `p95` беремо з бакетів, а не з квантилів:

```promql
histogram_quantile(0.95, sum by (le) (rate(predict_latency_seconds_bucket[5m])))
```

### Чому Histogram, а не Gauge/Summary

* **Gauge** зберігає лише останнє значення — між скрейпами (60 с) проходять
  сотні запитів, і ви побачите один випадковий.
* **Summary** рахує квантилі в кожному поді окремо, а квантилі не додаються:
  `p95` пода A і `p95` пода B не дають `p95` сервісу. На 2+ репліках — брехня.
* **Histogram** віддає `_bucket/_sum/_count`; бакети додаються між подами.

Бакети підібрані **за замірами**, не на око: 441 реальний запит дав ~5.8 мс
end-to-end (на нативному x86_64 очікувано 2-3 мс). Дефолтні бакети
`prometheus_client` починаються з 0.005 — усе впало б в один стовпчик.

Окрема пастка: на **чистому** Iris RandomForest дає впевненість рівно `1.0`
у 74% випадків. Щоб панель «Confidence distribution» не була одним стовпчиком,
генератор навантаження мусить слати **зашумлені** точки — це умова, а не прикраса.

## Логи

Один JSON-обʼєкт на рядок у stdout (JSON Lines), поля рівно за контрактом:

```json
{"ts":"2026-08-12T20:04:54+0000","level":"INFO","event":"predict",
 "request_id":"11090354f0cc...","input":{"sepal_length":5.1,"sepal_width":3.5,
 "petal_length":1.4,"petal_width":0.2},"prediction":"setosa","confidence":1.0,
 "latency_ms":3.22}
```

Запити в Loki:

```logql
{namespace="ml-demo", app="ml-model"} | json | confidence < 0.7
{namespace="ml-demo", app="ml-model"} | json | event = "validation_error"
```

**`request_id` не можна робити лейблом Loki.** Loki будує окремий стрім на кожну
унікальну комбінацію лейблів; унікальний ID на запит = мільйони стрімів = Loki
лягає. Лейбли — тільки низькокардинальні (`namespace`, `app`, `pod`), решта живе
в тілі JSON і фільтрується на льоту через `| json`. Це і є ключова різниця між
лейблами Prometheus і лейблами Loki.

## Що знадобиться маніфестам

* порт контейнера `8000`, порт Service `80`;
* проби: `httpGet /healthz` на 8000 (у образі **немає** `curl`, тільки httpGet);
* `securityContext: runAsNonRoot: true, runAsUser: 1000` — образ уже під uid 1000,
  і USER заданий **числом**, інакше kubelet падає з
  `container has runAsNonRoot and image has non-numeric user`;
* порт у Service **мусить мати імʼя** (`http`) — `ServiceMonitor` матчить порт
  за іменем, а не за номером;
* жодних PVC: у цьому кластері єдиний StorageClass `gp2` використовує вилучений
  in-tree провізіонер `kubernetes.io/aws-ebs`, тож будь-який PVC зависне в Pending.
