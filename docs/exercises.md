# Вправи: MLflow, дріфт і автоматизоване тренування

**Теми 9 і 10.** Вправи 1-8 — MLflow, Model Registry, виявлення дріфту
(`docs/09-mlflow-drift.md`). Вправи 9-12 — пайплайн Step Functions
(`docs/10-automated-training.md`).

Дванадцять вправ за зростанням складності. Усе виконується на живому стеку —
жодних вигаданих сервісів, усі імена реальні.

> **Правило номер один на весь курс:** дивіться колонку **READY**, а не STATUS.
> `Running` при `READY 1/3` означає, що под мертвий на дві третини, а ArgoCD
> при цьому напише `Synced/Healthy`.

---

## Перед початком

```bash
# 1. Увесь стек живий?
make status
# Очікувано в mlflow 4 поди READY 1/1: postgres-0, minio-*, mlflow-*, drift-exporter-*
# Якщо стека немає взагалі — make up (~10 хв)

# 2. Тунелі + таблиця з логінами і статусом кожного сервісу
make ports

# 3. Генератор трафіку увімкнений і БЕЗ дріфту
kubectl -n ml-demo set env deploy/load-generator DRIFT_SHIFT=0
kubectl -n ml-demo scale deploy/load-generator --replicas=1   # або make loadgen
```

**⚠️ MLflow на 5001, а не 5000, дріфт-експортер на 9101, а не 9100.** Порти
5000 і 7000 на macOS тримає AirPlay Receiver — тунель туди мовчки не встає, а
`curl` отримує 403 від AirTunes. Усі команди нижче вже враховують це.

### Шаблон, який знадобиться у вправах 3, 4 і 8

Одноразовий под з образом інструментів і **всіма креденшелами через один
`envFrom`**. Прочитайте один раз — далі просто копіюйте виклики `tool`.

```bash
# Реєстр беремо з ваших креденшелів, а не зашиваємо чужий номер акаунта.
IMG=$(aws sts get-caller-identity --query Account --output text).dkr.ecr.eu-central-1.amazonaws.com/mds06-mlflow-tools:v3

tool() {   # використання:  tool <імʼя-пода> <<'PY' ... PY
  kubectl -n mlflow run "$1" --rm -i --restart=Never --image="$IMG" \
    --overrides="{\"spec\":{\"containers\":[{\"name\":\"tool\",\"image\":\"$IMG\",\"imagePullPolicy\":\"Always\",\"stdin\":true,\"command\":[\"python\",\"-\"],\"envFrom\":[{\"secretRef\":{\"name\":\"mlflow-credentials\"}}],\"resources\":{\"requests\":{\"cpu\":\"100m\",\"memory\":\"384Mi\"},\"limits\":{\"memory\":\"1Gi\"}}}]}}"
}
```

Чому `envFrom`, а не пʼять окремих `env`: у Secret `mlflow-credentials` уже
лежать `MLFLOW_TRACKING_URI`, `MLFLOW_S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` — рівно те, що потрібно клієнту.
Три рядки замість двадцяти.

*Якщо `-i` не віддав вивід* (буває при повільному pull образу) — не боріться:
`kubectl -n mlflow logs <імʼя-пода>` після запуску без `--rm`.

### Контракт скриптів, на який спираються вправи

`apps/trainer/train.py` — єдине джерело правди; якщо імена розійшлися, вірте
коду. Job збирається з одного канонічного маніфеста `k8s/trainer/job.yaml`
(`envsubst` у `scripts/train.sh`).

| Що | Значення |
|---|---|
| Один Job = | **6 запусків MLflow** (сітка `GRID_N_ESTIMATORS` × `GRID_MAX_DEPTH`) + автоматична реєстрація найкращого |
| Змінні сітки | `GRID_N_ESTIMATORS` (`50,100,200`), `GRID_MAX_DEPTH` (`2,none` — слово `none` означає «без обмеження глибини») |
| Інші змінні | `MLFLOW_EXPERIMENT_NAME` (`iris-rf`), `MLFLOW_MODEL_NAME` (`iris-rf`), `TEST_SIZE` (`0.2`), `RANDOM_STATE` (`42`) |
| `log_param` | `n_estimators`, `max_depth`, `test_size`, `random_state` |
| `log_metric` | `accuracy`, `precision`, `recall`, `f1` (усі macro) |
| `log_artifact` | `confusion_matrix.png`, `reference.csv` |
| `log_model` | sklearn-модель під імʼям `model`, зі `signature` та `input_example` |
| Registry | найкращий за `(f1, accuracy)` → модель `iris-rf`, нова версія завжди |
| `PROMOTE_TO_CHAMPION` | `true` (дефолт, `make train`) → аліас `@champion` переїжджає одразу. `false` (пайплайн Теми 10) → версія реєструється БЕЗ аліаса, рішення за quality gate |
| Останній рядок stdout | подія `training_result` з `f1`, `champion_f1`, `promoted` — **контракт** із Lambda `evaluate` Теми 10 |

---

## Вправа 1. Своя сітка гіперпараметрів ⭐ (~20 хв)

### Мета
Побачити, що MLflow — це не «ще один UI», а місце, де десяток запусків стають
порівнюваними без жодного блокнота з нотатками.

### Що робити

Спершу базовий прогін — сітка за замовчуванням, 6 запусків в одному поді:

```bash
make train
```

Усередині `scripts/train.sh` підставляє змінні через `envsubst` в **один
канонічний маніфест** `k8s/trainer/job.yaml`, чекає завершення Job і друкує
таблицю результатів. Своя сітка — двома змінними:

```bash
# ЗАПУСКАЙТЕ ПО ОДНОМУ. Слоти подів у кластері на межі (див. docs/09-mlflow-drift.md):
# два Job одночасно = один Pending, і скрипт висітиме 5 хвилин, поки не здасться.
make train N=5,500 D=1,none      # зробіть сітку такою, щоб метрики РОЗІЙШЛИСЬ
```

Ще дві ручки того самого скрипта, які знадобляться далі:

```bash
EXPERIMENT=my-test make train    # в окремий експеримент MLflow
PROMOTE=false make train         # зареєструвати версію, але НЕ чіпати @champion
```

`PROMOTE=false` — рівно те, що робить пайплайн Теми 10. Запустіть його один раз
і подивіться на останній рядок виводу: замість `— champion` там буде
`— без аліаса (рішення за quality gate)`.

Далі — MLflow UI на http://localhost:5001: експеримент `iris-rf`. Через
**Columns** додати `n_estimators`, `max_depth`, `f1`; клік на заголовок
колонки — сортування.

### Що має вийти
10 запусків `FINISHED` (6 + 4). `make train` друкує по рядку на запуск, потім
`⭐ найкращий` і `📦 у реєстрі`. У розділі **Models** зʼявиться `iris-rf` з
аліасом `champion`.

### На що звернути увагу
- **`max_depth=1` — єдиний параметр, який тут по-справжньому щось ламає.**
  Одне розгалуження не може розділити три класи: f1 впаде до ~0.5-0.6. А от
  5 дерев проти 500 дадуть **однаковий** результат — Iris надто простий.
  Це і є цінність MLflow: він чесно показує, що ваша «оптимізація» нічого не
  змінила. Без нього ви б написали в звіті «підібрали 500 дерев» і не змогли б
  цього довести навіть собі.
- **Один Job = 6 запусків, а не 1.** Сітка крутиться в одному поді, тому й
  слот один. Це навмисно: у кластері з 34 слотами шість окремих Job — це шість
  Pending.
- **Другий прогін перевішує аліас `champion`.** Скрипт реєструє найкращого
  щоразу, тож після `tiny` champion може вказувати на іншу версію. У вправі 3
  ми на це подивимось уважніше — і саме тут захована найпоширеніша аварія
  Model Registry.
- `ttlSecondsAfterFinished: 1800` у `k8s/trainer/job.yaml` — не косметика: Job
  без нього лежить завершеним вічно і **займає запис в etcd**, а його под —
  слот, поки ви його не приберете. 1800 c, а не менше, тому що Step Functions
  Теми 10 читає статус Job уже ПІСЛЯ завершення.
- **Цей самий файл читає Terraform Теми 10** через `yamldecode()` і вставляє в
  крок `eks:runJob.sync`. Тобто пайплайн тренує рівно те саме, що ви щойно
  запустили руками. Раніше Job збирався двічі й по-різному — хірургією над YAML
  у python у двох скриптах, і два джерела правди розходились при першій зміні.
- Якщо Job у `ImagePullBackOff`: приватний ECR працює без `imagePullSecret`
  лише тому, що на node role висить `AmazonEC2ContainerRegistryReadOnly`.
  Перевірте, що тег у ECR збігається з тим, що підставив `train.sh`
  (`mds06-mlflow-tools:v3`) — зібрати: `make images`.
  Якщо в `CreateContainerConfigError` — немає Secret `mlflow-credentials`.

---

## Вправа 2. Порівняти два запуски і сказати, чому один кращий ⭐ (~15 хв)

### Мета
Навчитися не робити висновків із шуму. Це найчастіша помилка ML-звітів.

### Що робити

У UI: позначити чекбокси двох запусків → **Compare**. Подивитись таблицю
параметрів (розбіжності підсвічені) і Scatter/Parallel Coordinates.

Потім те саме через API — бо UI це лише «мордочка» до нього:

```bash
EXP=$(curl -s "http://localhost:5001/api/2.0/mlflow/experiments/get-by-name?experiment_name=iris-rf" \
      | jq -r '.experiment.experiment_id')
echo "experiment_id=$EXP"

curl -s -X POST http://localhost:5001/api/2.0/mlflow/runs/search \
  -H 'Content-Type: application/json' \
  -d "{\"experiment_ids\":[\"$EXP\"],\"max_results\":50,\"order_by\":[\"metrics.f1 DESC\"]}" \
| jq -r '.runs[] | [
    .info.run_id[0:8],
    (.data.params[]?|select(.key=="n_estimators")|.value),
    (.data.params[]?|select(.key=="max_depth")|.value),
    (.data.metrics[]?|select(.key=="f1")|.value|tostring),
    (.data.metrics[]?|select(.key=="accuracy")|.value|tostring)
  ] | @tsv' | column -t
```

### Що має вийти
Таблиця «run / n_estimators / max_depth / f1 / accuracy», відсортована за f1 —
**той самий порядок**, що в UI. Унизу опиняться запуски з `max_depth=1`, і
відрив у них буде великий. Решта рядків збіжаться до кількох однакових значень.

### На що звернути увагу
- **Порахуйте крок метрики.** `test_size=0.2` від 150 рядків = **30 тестових
  прикладів**, тобто accuracy може змінюватись лише кратно **1/30 = 0.0333**.
  Різниця між 0.9667 і 1.0000 — це **рівно один правильно вгаданий рядок**.
  Оголошувати переможця за такою різницею не можна. Це і є відповідь на питання
  «чому один кращий»: у більшості випадків **не кращий**, а щасливіший.
- **Скрипт вибирає найкращого за `(f1, accuracy)` — з tie-break, і це не
  бюрократія.** На Iris кілька конфігурацій регулярно дають ідентичні метрики;
  без другого ключа «найкращий» залежав би від порядку перебору в сітці, тобто
  від випадковості. Подивіться в логах Job рядок `best_run` і знайдіть його в
  таблиці — там майже завжди буде кілька рядків з таким самим f1.
- Що тоді вибирати серед однакових? **Простішу модель**: 50 дерев замість 500
  (швидше, менший артефакт, менше памʼяті в проді). MLflow цього за вас не
  зробить — `max()` не знає, що дешевше експлуатувати.
- `precision` і `recall` тут майже дорівнюють accuracy, бо класи збалансовані
  (`stratify=y`). Це не «зайві метрики», а демонстрація: метрику вибирають під
  дані, і на незбалансованих даних ці три цифри розійшлися б різко.
- API повертає **той самий** JSON, що малює UI. Це важливо: у CI ви ходите в
  API, а не клікаєте. Наступна тема (GitLab CI) саме про це.

---

## Вправа 3. Завантажити модель із Model Registry і зробити передбачення ⭐⭐ (~20 хв)

### Мета
Пройти шлях «зареєстрована версія → аліас → завантаження в чужому поді» і
своїми руками зробити найпоширенішу аварію Model Registry: **перевісити аліас**.

Реєструвати руками не треба — `train.py` уже зробив це у вправі 1. Тому
вправа не про «як зареєструвати», а про те, **що означає посилання
`models:/iris-rf@champion` для коду, який його читає**.

### Що робити

```bash
# 1. Що зараз у реєстрі: усі версії, їхні f1 і хто з них champion
tool versions <<'PY'
from mlflow import MlflowClient
c = MlflowClient()

champ = c.get_model_version_by_alias("iris-rf", "champion")
print(f"champion -> версія {champ.version}, run {champ.run_id[:8]}")

for mv in c.search_model_versions("name='iris-rf'"):
    m = c.get_run(mv.run_id).data
    print(f"  версія {mv.version:>2}  f1={m.metrics.get('f1'):.4f}  "
          f"n={m.params.get('n_estimators'):>3}  depth={m.params.get('max_depth')}"
          f"{'   <-- champion' if mv.version == champ.version else ''}")
PY

# 2. Завантажити З РЕЄСТРУ і передбачити — жодного run_id у коді
tool predict <<'PY'
import mlflow.sklearn, pandas as pd
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

FEATURES = ["sepal_length", "sepal_width", "petal_length", "petal_width"]
model = mlflow.sklearn.load_model("models:/iris-rf@champion")
print("модель:", type(model).__name__, "| дерев:", model.n_estimators,
      "| глибина:", model.max_depth)

d = load_iris()
X = pd.DataFrame(d.data, columns=FEATURES)
_, X_test, _, y_test = train_test_split(
    X, d.target, test_size=0.2, random_state=42, stratify=d.target)
print("accuracy на тих самих 30 рядках:", (model.predict(X_test) == y_test).mean())

one = pd.DataFrame([[5.1, 3.5, 1.4, 0.2]], columns=FEATURES)
print("одне передбачення:", d.target_names[model.predict(one)[0]])
PY

# 3. АВАРІЯ: перевісити champion на найгіршу версію (max_depth=1)
tool repoint <<'PY'
from mlflow import MlflowClient
c = MlflowClient()
worst = min(c.search_model_versions("name='iris-rf'"),
            key=lambda mv: c.get_run(mv.run_id).data.metrics.get("f1", 1.0))
c.set_registered_model_alias("iris-rf", "champion", worst.version)
print("champion тепер вказує на версію", worst.version,
      "f1 =", c.get_run(worst.run_id).data.metrics.get("f1"))
PY

# 4. Повторити крок 2 БЕЗ ЖОДНОЇ ЗМІНИ В КОДІ. Порівняти accuracy.
# 5. Повернути champion назад (версія з кроку 1) — і зробіть це самі,
#    змінивши команду з кроку 3.
```

### Що має вийти
Крок 2 дає accuracy ≈ 0.93-1.0. Після кроку 3 **той самий рядок коду**
`load_model("models:/iris-rf@champion")` віддає модель з `max_depth=1` і
accuracy ≈ 0.6. Код не змінився, дані не змінилися — змінився **вказівник**.

### На що звернути увагу
- **Це і є справжня небезпека Model Registry.** Аліас — глобальний змінюваний
  стан у проді. Один `set_registered_model_alias` тихо змінює поведінку всіх
  сервісів, що читають `@champion`, і в жодному Git-репозиторії про це не буде
  ні комміту, ні рядка. Саме тому в дорослих командах перевішування аліасів
  роблять з CI, а не з ноутбука.
- **`models:/iris-rf@champion`, а не `/Production`.** Stage-и (`None/Staging/
  Production/Archived`) у MLflow 3.x вважаються застарілими; аліаси
  переставляються атомарно й без переносу артефакту.
- **Модель тягнеться з MinIO, метадані — з PostgreSQL.** Вимкніть один із них
  (вправа 8) і подивіться, який саме крок падає: `load_model` спершу питає
  реєстр (Postgres), потім забирає файли (MinIO).
- `pd.DataFrame` з іменами колонок тут не косметика: модель залогована зі
  `signature`, і на голому numpy-масиві MLflow видасть попередження про
  невідповідність схеми. Спробуйте передати список — побачите його.
- Порівняйте передбачення з живим сервісом Теми 8 — це та сама модель
  (за умови, що champion на нормальній версії):
  ```bash
  kubectl -n ml-demo port-forward svc/ml-model 8000:80 &
  curl -s -X POST localhost:8000/predict -H 'Content-Type: application/json' \
    -d '{"sepal_length":5.1,"sepal_width":3.5,"petal_length":1.4,"petal_width":0.2}' | jq .
  ```
  Відповіді мусять збігтися. Якщо ні — у вас **дві різні моделі під однією
  назвою**, і це найгірший сценарій MLOps.

---

## Вправа 4. Знайти артефакт у MinIO і зрозуміти адресацію ⭐⭐ (~25 хв)

### Мета
Побачити, що «артефакт MLflow» — це просто обʼєкт у S3, а MLflow тримає лише
посилання. Плюс зробити те, чого 40 рядків scipy не дадуть: багатий HTML-звіт
Evidently.

### 4а. Від UI до сирого обʼєкта

```bash
# 1. У MLflow UI: run -> Artifacts -> confusion_matrix.png. Скопіювати шлях,
#    який показує UI (кнопка копіювання поруч з назвою).

# 2. Той самий обʼєкт очима MinIO. Креденшели беремо з того самого Secret.
MC_USER=$(kubectl -n mlflow get secret mlflow-credentials -o jsonpath='{.data.rootUser}' | base64 -d)
MC_PASS=$(kubectl -n mlflow get secret mlflow-credentials -o jsonpath='{.data.rootPassword}' | base64 -d)

kubectl -n mlflow run mc --rm -it --restart=Never \
  --image=quay.io/minio/mc:RELEASE.2024-11-21T17-21-54Z --command -- sh -c "
  mc alias set local http://minio.mlflow.svc.cluster.local:9000 '$MC_USER' '$MC_PASS' &&
  mc ls --recursive local/mlflow-artifacts | head -40 &&
  echo '--- сумарний обсяг ---' &&
  mc du local/mlflow-artifacts"

# 3. Порівняти з тим, що каже PostgreSQL
kubectl -n mlflow exec sts/postgres -- \
  psql -U mlflow -d mlflow -c \
  "select run_uuid, artifact_uri from runs limit 5;"
```

### Що має вийти
Ключі виду `<experiment_id>/<run_id>/artifacts/confusion_matrix.png` (у MLflow 3
для моделей структура інша — `.../models/...`). У Postgres — `artifact_uri`, що
починається на `s3://mlflow-artifacts/`. **Порівняйте символ за символом.**

### На що звернути увагу
- **Не вірте документації — прочитайте реальний ключ.** Розкладка артефактів
  міняється між мажорними версіями MLflow; `mc ls --recursive` завжди правда.
- **PostgreSQL зберігає лише РЯДОК-посилання**, самі байти лежать у MinIO. Тому
  бекап однієї бази без бакета — це бекап каталогу без бібліотеки. Перевірте:
  видаліть обʼєкт через `mc rm` і відкрийте артефакт у UI — запис у списку
  залишиться, а завантаження впаде.
- У MinIO UI (порт 9001) керування бакетами може бути недоступним: після
  ~2025-04 адмін-функції вирізано в платний AIStor. Наш чарт пінить
  `RELEASE.2024-12-18`, де консоль ще повна. **Не підіймайте `image.tag`.**
  Bucket однаково створюється декларативно, `mc` працює завжди.

### 4б. Evidently: багатий звіт як артефакт (слайд 35, лабораторна частина)

**Evidently НЕМА в образі `mds06-mlflow-tools:v3`** — і це свідомо: +500-700 MiB
(pyarrow, plotly, litestar, statsmodels, nltk) заради HTML, який у гарячому
шляху не потрібен. У `apps/trainer/requirements.txt` рядок `evidently==0.7.21`
лежить закоментованим.

Тому запускаємо **зі свого ноутбука** — і саме так ви на власній шкірі
відчуєте, чому `proxiedArtifactStorage: false` вимагає доступу до MinIO.

```bash
# ТРИ тунелі. MinIO тут ОБОВʼЯЗКОВИЙ: при proxiedArtifactStorage: false
# артефакт вантажить КЛІЄНТ напряму в S3, а не сервер MLflow.
kubectl -n mlflow  port-forward svc/mlflow 5001:80 &
kubectl -n mlflow  port-forward svc/minio  9000:9000 &
kubectl -n logging port-forward svc/loki   3100:3100 &

python3 -m venv /tmp/ev && . /tmp/ev/bin/activate
pip install evidently==0.7.21 mlflow==3.15.1 boto3==1.43.72 \
            scikit-learn==1.9.0 scipy==1.18.0 pandas==2.3.3

export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
export MLFLOW_S3_ENDPOINT_URL=http://127.0.0.1:9000
export AWS_DEFAULT_REGION=eu-central-1
export AWS_ACCESS_KEY_ID=$(kubectl -n mlflow get secret mlflow-credentials -o jsonpath='{.data.rootUser}' | base64 -d)
export AWS_SECRET_ACCESS_KEY=$(kubectl -n mlflow get secret mlflow-credentials -o jsonpath='{.data.rootPassword}' | base64 -d)
export LOKI_URL=http://127.0.0.1:3100        # drift_exporter читає це з env
export DO_NOT_TRACK=1                        # iterative-telemetry «дзвонить додому»

cd apps/drift-exporter && python - <<'PY'
import mlflow, pandas as pd
from evidently import Report
from evidently.presets import DataDriftPreset
from drift_exporter import fetch_current, load_reference   # той самий код, що в кластері

ref_dict, _ = load_reference()
reference = pd.DataFrame(ref_dict)
cur_dict, _ = fetch_current()          # те саме вікно з Loki, що бачить експортер
current = pd.DataFrame(cur_dict)
print("еталон:", reference.shape, "поточне вікно:", current.shape)
assert len(current) >= 30, "вікно замале — увімкніть генератор і почекайте 2 хв"

# УВАГА: порядок ПОЗИЦІЙНИЙ і зворотний до звички — run(current, reference).
report = Report([DataDriftPreset(method="ks")], include_tests="True")
ev = report.run(current, reference)
ev.save_html("/tmp/evidently.html")

mlflow.set_experiment("iris-rf")
with mlflow.start_run(run_name="evidently-report"):
    mlflow.log_artifact("/tmp/evidently.html")
    mlflow.log_metric("current_window_size", len(current))
print("готово: MLflow UI -> run evidently-report -> Artifacts")
PY
```

Далі: MLflow UI → run `evidently-report` → Artifacts → `evidently.html` →
**Download**. Порівняйте його вердикт із панеллю 7 дашборда.

*Варіант для тих, кому потрібен Evidently у кластері:* розкоментувати
`evidently==0.7.21` у `apps/trainer/requirements.txt`, зібрати образ як
**`:v3`** (не перезаписуючи `v2` — граблі №6) і запускати через `tool`. Ціна —
образ ~1.8 GiB на кожній ноді.

### На що звернути увагу
- **Забудете тунель до MinIO — отримаєте
  `botocore.exceptions.EndpointConnectionError` або `NoSuchBucket`**, і це не
  баг, а найголовніша архітектурна деталь теми: `MLFLOW_S3_ENDPOINT_URL` на
  СЕРВЕРІ вам не допоможе, бо вантажить клієнт. Спробуйте навмисно вбити
  port-forward до 9000 і подивитись на помилку — вона вам ще зустрінеться.
- **`run(current, reference)`, а не навпаки.** Переставите — для KS отримаєте
  той самий p (тест симетричний), але для несиметричних метрик цифри поїдуть, і
  помилка буде **тихою**.
- API Evidently 0.7.x **несумісний з усіма туторіалами**: немає ні
  `from evidently.report import Report`, ні `metric_preset`, ні `ColumnMapping`,
  ні `as_dict()` (тепер `.dict()`).
- Тут Evidently дає те, чого немає в експортері: 100+ метрик і інтерактивні
  графіки. І тут же видно, чому його немає в експортері: цей самий звіт нічого
  не віддає в Prometheus — **готового експортера «Evidently → Prometheus» не
  існує в жодній версії**, стрілку зі слайда 35 пише інженер руками.
- HTML-звіт може бути кілька МБ. Це нормально для артефакту і зовсім не
  нормально для метрики.

---

## Вправа 5. Викликати дріфт і зловити момент на дашборді ⭐⭐ (~25 хв)

### Мета
Побачити своїми очима, як p-value падає на девʼять порядків, і зрозуміти, чому
дашборд реагує **не миттєво**.

### Що робити

```bash
# 0. Зафіксувати «до». Дивимось сирі метрики, а не тільки картинку.
curl -s localhost:9101/metrics | grep -E '^drift_|^current_window|^prediction_class'
date -u +%H:%M:%S            # запишіть цей час, він потрібен на дашборді

# 1. Лог експортера в окремому терміналі — раз на 60 с там зʼявляється рядок
kubectl -n mlflow logs -f deploy/drift-exporter

# 2. УВІМКНУТИ ДРІФТ
kubectl -n ml-demo set env deploy/load-generator DRIFT_SHIFT=0.8
date -u +%H:%M:%S            # мить T0

# 3. Спостерігати
watch -n 20 "curl -s localhost:9101/metrics | grep -E '^drift_(detected|p_value)'"

# 4. Через ~15 хв повернути норму
kubectl -n ml-demo set env deploy/load-generator DRIFT_SHIFT=0
```

У Grafana: дашборд **«Якість моделі — дріфт даних і прогнозів»**, діапазон
`now-1h`, `refresh 30s`. Головна — панель 6 (state timeline).

### Що має вийти

| Час від T0 | Що видно |
|---|---|
| 0-1 хв | нічого. Под генератора перестворюється (~10 с), вікно ще майже все «чисте» |
| 2-4 хв | p-value на панелі 7 починають опускатися; `petal_width` і `sepal_width` першими |
| 4-6 хв | на панелі 6 рядки по черзі стають червоними; setosa зникає зі стеку панелі 9 |
| ~14 хв | вікно 10 хв повністю зсунуте: p ≈ 1e-11 і нижче, усі 5 рядів червоні |
| після `DRIFT_SHIFT=0` | **дашборд НЕ зеленіє одразу** — ще ~10 хв у вікні лежать старі записи |

### На що звернути увагу
- **Порядок спрацювання не випадковий:** зсув +0.8 однаковий для всіх ознак, але
  `petal_width` має природний розкид ~0.76, а `sepal_length` ~0.83 — той самий
  зсув «у сигмах» різний, тому й p падають не одночасно.
- **`feature="prediction"` червоніє останнім.** Хі-квадрату потрібно, щоб
  змінилися самі прогнози, а не входи. Це різні речі: Data Drift може бути без
  Prediction Drift (модель стійка) — і саме тоді сперечаються, чи треба
  перенавчати.
- **p-value під нормою ШУМИТЬ у діапазоні 0.1-0.9.** Це не «майже дріфт», це
  визначення p-value: під H0 воно рівномірно розподілене на [0,1]. Студент, який
  цього не знає, панікує від p=0.12.
- **Лаг виявлення = розмір вікна.** 10 хв вікна означає 10 хв на повне
  «протравлення» і стільки ж на повернення. Хочете швидше — коротше вікно, але
  тоді менше зразків і тест слабший. Це реальний інженерний компроміс, а не
  налаштування «щоб красивіше».
- Бонус: `kubectl -n ml-demo scale deploy/load-generator --replicas=0`.
  `current_window_size` падає нижче 30, панель 5 стає «НІ», а p-value
  **ЗАМИРАЮТЬ**, не скидаючись у нуль. Дашборд, який зеленіє від відсутності
  даних, — небезпечніший за червоний.

---

## Вправа 6. Свій PromQL: дріфт лише по одній ознаці ⭐⭐⭐ (~20 хв)

### Мета
Перестати боятися векторного зіставлення в PromQL. Це те місце, де 90% людей
пишуть вираз, що «нічого не повертає», і думають, що метрик немає.

### Що робити
У Grafana → **Explore** → datasource Prometheus. Написати вирази, які дають
`1` (або непорожній результат) рівно у зазначеній ситуації.

**Завдання 6.1.** Дріфт є **тільки** по `petal_width` і ні по чому іншому.

**Завдання 6.2.** Дріфт по вхідних даних є, а по прогнозах — ні (найцікавіший
з практичного погляду випадок).

**Завдання 6.3.** «Стійкий» дріфт: `petal_length` червоний **у кожній**
перевірці за останні 10 хвилин — саме таке годиться в алерт.

**Завдання 6.4.** Скільки ознак у дріфті, але з урахуванням того, що перевірка
могла застаріти: результат мусить бути порожнім, якщо остання перевірка була
понад 5 хвилин тому.

<details>
<summary>Відповіді (спершу спробуйте самі)</summary>

```promql
# 6.1 — ключове тут on(): sum() віддає вектор БЕЗ лейблів, а drift_detected
# має лейбл feature. Без on() PromQL не знаходить збігу і повертає ПОРОЖНЬО —
# найпоширеніша помилка, і виглядає вона як «метрики немає».
drift_detected{feature="petal_width"} == 1 and on() sum(drift_detected) == 1

# 6.2 — unless = різниця множин. ignoring(feature) потрібен, бо лейбл feature
# у лівої і правої частини РІЗНИЙ, а зіставляти треба «весь ряд проти ряду».
sum(drift_detected{feature!="prediction"}) > 0
  unless on() drift_detected{feature="prediction"} == 1

# 6.3 — min_over_time: якщо хоч в одній точці за 10 хв був 0, мінімум = 0.
# Саме це відсіює виміряні 2% хибних спрацювань хі-квадрата.
min_over_time(drift_detected{feature="petal_length"}[10m]) == 1

# 6.4 — and on() з умовою свіжості. Порівняйте з тим, що дає той самий вираз
# без цієї страховки, коли ви вбʼєте експортер (вправа 8).
sum(drift_detected) and on() (time() - drift_check_timestamp_seconds < 300)
```
</details>

### Що має вийти
Кожен вираз у Explore видає результат тоді, коли має, і **порожньо** тоді, коли
умова не виконана. Перевірте обидва стани: увімкніть дріфт (вправа 5) і
вимкніть.

### На що звернути увагу
- **`on()` з порожніми дужками = «зіставляй без лейблів».** Це не магія:
  бінарні оператори в PromQL за замовчуванням вимагають ІДЕНТИЧНОГО набору
  лейблів з обох боків. `sum()` лейбли зʼїдає — отже без `on()` збігу не буде
  ніколи.
- **`== 1` — це фільтр, а не булеве значення.** `drift_detected == 1` прибирає
  ряди зі значенням 0. Щоб отримати саме 0/1, потрібен модифікатор `bool`
  (як у панелі 5 дашборда).
- **`unless` не має протилежності «and not» у PromQL** — саме `unless` і є
  «and not» для векторів.
- Перевірте свій вираз ще й на випадку, коли ознак у дріфті **дві**: 6.1 мусить
  стати порожнім. Вираз, який спрацьовує «завжди, коли є хоч якийсь дріфт», —
  типова помилка й непомітна, поки дріфт один.

---

## Вправа 7. Зіставити метрику дріфту з конкретними запитами в Loki ⭐⭐⭐ (~25 хв)

### Мета
Пройти шлях «на дашборді червоне» → «ось ті самі 30 запитів, через які воно
червоне». Без цього кроку моніторинг залишається гаданням.

### Що робити
Grafana → **Explore** → datasource **Loki**.

```logql
# 1. Скільки взагалі передбачень у вікні
count_over_time({app="ml-model"} | json | event="predict" [10m])

# 2. Середнє значення ознаки в часі — ТА САМА величина, яку KS порівнює з еталоном
avg_over_time({app="ml-model"} | json | unwrap input_petal_width [5m])

# 3. Конкретні запити, яких не могло бути в еталоні
#    (у train-split максимальний petal_width = 2.5)
{app="ml-model"} | json | input_petal_width > 2.5

# 4. Ті самі рядки, але читабельно
{app="ml-model"} | json | input_petal_width > 2.5
  | line_format "{{.input_petal_width}} -> {{.prediction}} ({{.confidence}})"

# 5. Прогнози, у яких модель стала невпевненою
{app="ml-model"} | json | confidence < 0.7
```

Далі: у Grafana поставте панель дріфту і панель Loki на **один діапазон часу** і
знайдіть мить, коли `avg_over_time` стрибнув, а `drift_p_value` почав падати.

### Що має вийти
- Пункт 2 до дріфту показує ~1.2 (справжнє середнє `petal_width` в Iris), після
  `DRIFT_SHIFT=0.8` — ~2.0.
- Пункт 3 до дріфту віддає **майже нічого**, після — суцільний потік.
- Стрибок середнього в Loki передує падінню p-value на дашборді: логи бачать
  зміну **одразу**, тест — коли її стане достатньо у вікні.

### На що звернути увагу
- **`input_petal_width`, а не `input.petal_width`.** Парсер `| json` у Loki
  **розплющує** вкладені обʼєкти через `_`. Саме через цю неоднозначність
  `drift_exporter.py` свідомо НЕ використовує `| json`, а розбирає рядок
  `json.loads` у Python: так код не залежить від того, як Loki вирішив назвати
  поле.
- **`unwrap` працює лише з числовими полями** і лише після парсера. Спробуйте
  `unwrap prediction` — отримаєте помилку, і це правильно: `setosa` не число.
- **Loki у нас на `emptyDir`.** Рестарт його пода стирає історію, тобто ваше
  вікно. Для 10 хвилин це прийнятно, у проді Loki був би з томом — і це, до
  речі, четверте місце в курсі, де все впирається в те саме зламане сховище.
- **`{app="ml-model"}` — низькокардинальний лейбл, і так має бути.**
  `request_id` живе в тілі логу; якби він був лейблом, Loki створив би окремий
  стрім на кожен запит і ліг. Перевірте, скільки стрімів зараз:
  `kubectl -n logging port-forward svc/loki 3100:3100` і
  `curl -s 'localhost:3100/loki/api/v1/labels' | jq .`

---

## Вправа 8. Зламати навмисно ⭐⭐⭐⭐ (~35 хв)

### Мета
Дізнатися, що саме перестає працювати і **що при цьому бреше**. Найцінніша
вправа теми: у проді ви побачите не «Postgres лежить», а «MLflow не
відкривається», і між цими двома фразами — вся діагностика.

### 8а. Вбити PostgreSQL

```bash
# Селектори чужих чартів не вгадуйте — подивіться:
kubectl -n mlflow get pods --show-labels

kubectl -n mlflow scale statefulset/postgres --replicas=0
kubectl -n mlflow get pods -w        # postgres-0 зникає

# 1. Що робить MLflow UI? Оновіть сторінку на localhost:5001
# 2. Под MLflow при цьому:
kubectl -n mlflow get pods -l app.kubernetes.io/name=mlflow    # ДИВІТЬСЯ READY
kubectl -n mlflow logs deploy/mlflow --tail=20

# 3. А тепер найважливіше — перезапустити MLflow, поки бази немає
kubectl -n mlflow delete pod -l app.kubernetes.io/name=mlflow
kubectl -n mlflow get pods -w        # застрягне в Init:0/2
kubectl -n mlflow logs -l app.kubernetes.io/name=mlflow -c dbchecker

# 4. Повернути
kubectl -n mlflow scale statefulset/postgres --replicas=1
kubectl -n mlflow get pods -w        # MLflow сам доїде до Running
```

**Що має вийти:** UI віддає 500 / «Internal Server Error», але сам под
залишається `Running 1/1` — **liveness-проба його не вбиває, бо HTTP-сервер
живий**. Після рестарту под чесно стоїть в `Init`, а не падає в CrashLoop: це
init-контейнер `dbchecker` чекає `PGHOST:PGPORT` з фібоначчі-бекофом.

**На що звернути увагу:**
- «Под Running» ≠ «сервіс працює». Ваша liveness-проба перевіряє те, що
  перевіряє, і ні на що більше не претендує.
- ArgoCD у цей момент показує `Progressing`, **а не `Degraded`** — тобто
  GitOps-панель теж не є монітором доступності.
- Той самий `dbchecker` — це «пояс окремо від підтяжок»: sync-wave вже
  гарантував порядок, але вручну зламаний стан він теж переживає.

### 8б. Вбити MinIO — і побачити, що дані живі (це і є суть теми)

```bash
kubectl -n mlflow delete pod -l app=minio
kubectl -n mlflow get pods -w
# Recreate: спершу под ПОВНІСТЮ зникає, лише потім зʼявляється новий
kubectl -n mlflow get pvc                    # PVC той самий, Bound, вік не змінився

# Артефакти на місці?
# (повторіть mc ls з вправи 4а — список ідентичний)

# А тепер контраст. Тема 8 їде на emptyDir:
kubectl -n monitoring delete pod -l app.kubernetes.io/name=prometheus
# і подивіться на будь-яку панель дашборда Теми 8 — історія зникла
```

**Що має вийти:** артефакти MLflow **переживають** рестарт пода, метрики
Prometheus — **ні**. Це та сама відмінність, яку презентація описує словами
«зберігайте моделі, щоб не втрачати роботу», лише тепер вона у вас на екрані.

**На що звернути увагу:**
- Стратегія `Recreate` тут обовʼязкова: RWO-том EBS не можна змонтувати у два
  поди, і дефолтний `RollingUpdate` дав би `Multi-Attach error` назавжди.
- Подивіться, на якій ноді зʼявився новий под: `kubectl -n mlflow get pod -o wide`.
  Він **мусить** бути в тій самій AZ, що том. Якби планувальник обрав іншу —
  `node(s) had volume node affinity conflict`, і виглядало б це як «кластер
  зламався».

### 8в. Довести, що StorageClass gp2 мертвий

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: gp2-dead
  namespace: mlflow
spec:
  storageClassName: gp2
  accessModes: ["ReadWriteOnce"]
  resources: { requests: { storage: 1Gi } }
---
apiVersion: v1
kind: Pod
metadata:
  name: gp2-dead
  namespace: mlflow
spec:
  restartPolicy: Never
  containers:
    - name: writer
      image: public.ecr.aws/docker/library/busybox:1.37
      command: ["sh", "-c", "echo ok > /data/ok && sleep 60"]
      resources:
        requests: { cpu: 10m, memory: 16Mi }
        limits:   { memory: 32Mi }
      volumeMounts: [{ name: data, mountPath: /data }]
  volumes:
    - name: data
      persistentVolumeClaim: { claimName: gp2-dead }
EOF

kubectl -n mlflow describe pvc gp2-dead     # ← ЧИТАЙТЕ БЛОК Events
kubectl -n mlflow describe pod gp2-dead
kubectl get sc gp2 -o jsonpath='{.provisioner}'; echo

# ПРИБРАТИ ОБОВʼЯЗКОВО
kubectl -n mlflow delete pod gp2-dead --force
kubectl -n mlflow delete pvc gp2-dead
```

**Що має вийти:** PVC назавжди `Pending`. У Events **немає ані
`ProvisioningFailed`, ані `Provisioning`, ані згадки роботи жодного
провізіонера** — максимум повторюване «waiting for a volume to be created».
Виписайте формулювання, яке побачили, дослівно: **тиша в подіях = ніхто не
взявся за роботу**, і це відбиток, за яким цю поломку впізнають за 5 секунд.
Порівняйте з `gp3`, де відразу зʼявляються `Provisioning` /
`ProvisioningSucceeded` від `ebs.csi.aws.com_...`.

**На що звернути увагу:**
- `provisioner` = `kubernetes.io/aws-ebs` — in-tree плагін, **вилучений з
  Kubernetes у 1.31**. На 1.34 цей рядок не обробляє НІХТО: ні
  kube-controller-manager (коду немає), ні CSI-драйвер (не його імʼя).
- Спробуйте «полагодити» клас:
  `kubectl patch sc gp2 -p '{"provisioner":"ebs.csi.aws.com"}'` — отримаєте
  `field is immutable`. Єдиний шлях — новий обʼєкт з новим імʼям, і саме тому
  наш клас зветься `gp3`, а не `gp2`.

### 8г. Ротувати пароль — і побачити, що це не працює

```bash
NEW=$(openssl rand -hex 16)
kubectl -n mlflow patch secret mlflow-credentials \
  -p "{\"stringData\":{\"POSTGRES_PASSWORD\":\"$NEW\"}}"

kubectl -n mlflow rollout restart statefulset/postgres
kubectl -n mlflow delete pod -l app.kubernetes.io/name=mlflow
kubectl -n mlflow logs -l app.kubernetes.io/name=mlflow --tail=20
```

**Що має вийти:** `FATAL: password authentication failed for user "mlflow"`.
Postgres **не змінив** пароль, а MLflow уже надсилає новий.

**Лікування (обидва варіанти варто зробити):**
```bash
# А. Змінити пароль там, де він реально живе — у самій БД
kubectl -n mlflow exec sts/postgres -- \
  psql -U postgres -c "ALTER USER mlflow PASSWORD '$NEW';"
kubectl -n mlflow delete pod -l app.kubernetes.io/name=mlflow

# Б. (руйнівний, лише як демонстрація) знести том разом з даними.
# Ім'я PVC у volumeClaimTemplate НЕ ВГАДУЙТЕ — спитайте кластер:
# kubectl -n mlflow get pvc
# kubectl -n mlflow delete sts/postgres --cascade=orphan
# kubectl -n mlflow delete pvc <ім'я-з-попередньої-команди>
# ...і зверніть увагу: у чарті стоїть whenDeleted: Retain, тобто цей PVC
# НЕ зникає сам разом зі StatefulSet. Це навмисно — щоб selfHeal ArgoCD
# не зніс експерименти. Ціна: прибирати треба руками, інакше це рахунок.
```

**На що звернути увагу:**
- Офіційний образ postgres виконує `docker-entrypoint-initdb.d` **тільки коли
  PGDATA порожній**. Пароль записано у файли БД при першому старті; правка
  Kubernetes Secret на вже проініціалізований том не впливає **взагалі ніяк**.
- Це і є чесна ціна підходу «один Secret на все» зі слайда 23: він зручний, але
  ротація в stateful-сервісі — окрема процедура, а не `kubectl patch`.
- Саме тут стає видно, чому в продакшні беруть External Secrets Operator або
  Vault: не «щоб не тримати пароль у Git» (його там і немає), а щоб **ротація
  була процесом**, а не ручною операцією о третій ночі.

---

# Тема 10. Автоматизоване тренування

Вправи 9-12 працюють з пайплайном зі `docs/10-automated-training.md`:
GitHub Actions → Step Functions → Job у EKS → MLflow → quality gate → прод.

## Перед вправами 9-12

Пайплайн має бути розгорнутий, а стек Тем 8-9 — живий:

```bash
make up            # якщо ще не піднято
make pipeline-up   # 17 ресурсів: 3 Lambda, state machine, 3 ролі IAM, Access Entry
make pipeline-run  # контрольний прогін: усі шість станів мусять бути зелені
```

Дві речі, які економлять години в цих вправах:

- **`lambdas/*/handler.py` тестується без AWS.** У `terraform/training-pipeline/lambdas.tf`
  ZIP збирається з `source_file` — тобто **однієї** `handler.py`, без залежностей.
  Через це будь-яку логіку можна прогнати локально: `cd lambdas/evaluate && python3 test_handler.py`.
  Прогін у хмарі коштує 2-4 хвилини, локальний — 0.2 секунди.
- **`make pipeline-up` перезаллє Lambda автоматично.** `source_code_hash` рахується
  з ZIP, тож правка `handler.py` → `terraform apply` бачить зміну. Кластер і стек
  Тем 8-9 при цьому не чіпаються.

Дивитись, що саме сталося в Lambda:

```bash
aws logs tail /aws/lambda/mds06-evaluate --since 10m
aws logs tail /aws/lambda/mds06-validate --since 10m
```

---

## Вправа 9. Абсолютна підлога f1 у quality gate ⭐⭐ (~30 хв)

### Мета
Побачити діру в gate, який дивиться ЛИШЕ на різницю: ланцюжок дрібних
«покращень» може повільно зʼїхати вниз, і кожен окремий крок при цьому
формально законний.

### Що робити

**Крок 1 — відтворити діру.** Спершу зробіть свідомо погану модель чемпіоном,
а потім промоутьте трохи менш погану:

```bash
make train N=10 D=1              # PROMOTE=true за дефолтом -> @champion з f1 ~0.5-0.65
make pipeline-run N=500 D=1      # 500 дерев глибиною 1: краще за 10, але все одно жах
```

Чинний gate скаже **ПРОМОУТ**: `delta ≥ 0.001` виконано. Прод тепер тримає
модель, яка вгадує гірше за монетку на двох класах із трьох. Формально все
правильно — і саме це проблема.

*Якщо приріст вийшов відʼємним і ви отримали `ВІДХИЛЕНО` за дельтою — це теж
нормально (обидві моделі однаково погані). Повторіть із `N=1000 D=1`.*

**Крок 2 — додати підлогу.** Файл `lambdas/evaluate/handler.py`, поруч із
`MIN_DELTA`:

```python
# Абсолютна межа: нижче неї модель не їде в прод НІКОЛИ, навіть якщо вона
# краща за чинну. Дельта захищає від деградації відносно чемпіона; підлога —
# від ланцюжка законних дрібних кроків, який сповз у прірву.
MIN_F1 = float(os.getenv("MIN_F1", "0.9"))
```

і перевірка в `handler()` — **ДО** гілки `if champion is None`, одразу після
рядка `champion = result.get("champion_f1")`:

```python
    # delta рахуємо навіть коли відхиляємо: саме тоді вона найцікавіша
    # (метрика F1Delta у CloudWatch і рядок у виводі pipeline-run).
    d = None if champion is None else round(f1 - float(champion), 6)

    if f1 < MIN_F1:
        return {"promote": False,
                "reason": f"f1 {f1:.4f} нижче абсолютної підлоги {MIN_F1}",
                "version": str(result["version"]), "f1": f1,
                "champion_f1": champion, "delta": d,
                "accuracy": float(result.get("accuracy", 0)),
                "run_id": result.get("run_id", "")}
```

⚠️ **Два місця, де це ламається, і обидва — зміст вправи.**

1. **Порядок.** Поставите підлогу після `if champion is None` — і **перша**
   модель у порожньому реєстрі обійде її, бо та гілка робить `return` раніше.
   Дірка лишиться рівно там, де вона найнебезпечніша: на першому запуску в
   новому середовищі.
2. **`"delta": None` замість `d`.** Наявний тест
   `handler(logs(result(f1=0.88, champion_f1=0.93)))` очікує `delta < 0` і
   впаде з `TypeError: '<' not supported between instances of 'NoneType' and
   'int'`. Це не причіпка тесту: 0.88 нижче підлоги, тому нова гілка
   перехоплює цей випадок — і з `None` метрика `F1Delta` у CloudWatch зникла б
   саме тоді, коли вона найцікавіша. Спробуйте спершу з `None`, подивіться на
   падіння, потім полагодьте.

**Крок 3 — самоперевірка.** Додайте в `lambdas/evaluate/test_handler.py`:

```python
# ── краща за чинну, але нижче абсолютної підлоги -> ВІДХИЛИТИ ──
out = handler(logs(result(f1=0.62, champion_f1=0.55)), None)
assert out["promote"] is False, out
assert "підлоги" in out["reason"], out

# ── перша модель теж не обходить підлогу ──
out = handler(logs(result(f1=0.85, champion_f1=None)), None)
assert out["promote"] is False, out
```

```bash
cd lambdas/evaluate && python3 test_handler.py
```

**Крок 4 — застосувати й перевірити наживо:**

```bash
make pipeline-up                 # перезаллє Lambda: source_code_hash змінився
make pipeline-run N=500 D=1      # тепер ВІДХИЛЕНО
make train                       # повернути нормального чемпіона (f1 ~0.96)
```

### Що має вийти
Той самий прогін `N=500 D=1`, який до правки давав `✅ ПРОМОУТ`, тепер дає
`⛔ ВІДХИЛЕНО` з причиною `f1 0.6xxx нижче абсолютної підлоги 0.9`. Виконання
Step Functions — **SUCCEEDED**, гілка `ModelRejected`. Локально —
`✅ quality gate: 7 перевірок пройдено` плюс ваші дві.

### На що звернути увагу
- **Дельта і підлога ловлять різні аварії.** Дельта — «нова гірша за чинну».
  Підлога — «обидві погані». Жодна з них не замінює іншу, і саме тому в
  дорослому gate їх завжди дві.
- **Значення 0.9 — не істина, а рішення продукту.** На Iris нормальна модель
  дає 0.93-1.0, тож 0.9 відсікає лише поламане. На незбалансованих реальних
  даних 0.9 macro-f1 могло б бути недосяжним, і підлога заблокувала б усе.
  Число має приходити від того, хто знає ціну помилки, а не з README.
- **`os.getenv("MIN_F1", "0.9")` — навмисно.** Хочете керувати ним із
  Terraform, як `MIN_DELTA`: додайте `variable "min_f1"` у `variables.tf` і
  `MIN_F1 = tostring(var.min_f1)` у мапу `local.lambdas.evaluate.env`
  (`lambdas.tf`, рядок з `MIN_DELTA`). Дефолт у коді при цьому лишається
  страховкою: Lambda без змінної не стає беззубою.
- Порівняйте, скільки коштувала перевірка локально (0.2 c) і в хмарі (~3 хв
  на прогін). Це та сама теза, що й у ValidateParams, лише про тести.

---

## Вправа 10. Gate, який дивиться на КОЖЕН клас ⭐⭐⭐ (~50 хв, з них ~12 — збірка образу)

### Мета
Зрозуміти, чому macro-f1 — усереднення, і що воно ховає. Модель може підняти
середню f1 і водночас повністю провалити один клас: у медицині це «навчились
краще розпізнавати здорових, перестали бачити хворих».

### 10а. Половина в gate — без збірки образу (~15 хв)

Спершу навчіть gate читати поле, якого ще немає. Так ви перевірите логіку за
секунди, а не за 12 хвилин збірки.

`lambdas/evaluate/handler.py`:

```python
MIN_CLASS_F1 = float(os.getenv("MIN_CLASS_F1", "0.8"))
```

і сама перевірка в `handler()` — **одразу за підлогою з вправи 9** (`d` там
уже пораховано):

```python
    # У логах може не бути цього поля — старий образ. Тоді перевірку
    # ПРОПУСКАЄМО, а не валимо прогін: gate не має ламатись від того, що
    # тренувальний образ ще не оновили.
    per_class = result.get("f1_per_class")
    if per_class and min(per_class.values()) < MIN_CLASS_F1:
        worst = min(per_class, key=per_class.get)
        return {"promote": False,
                "reason": f"клас «{worst}»: f1 {per_class[worst]:.4f} < {MIN_CLASS_F1}",
                "version": str(result["version"]), "f1": f1,
                "champion_f1": champion, "delta": d,
                "accuracy": float(result.get("accuracy", 0)),
                "run_id": result.get("run_id", "")}
```

Тест у `test_handler.py` — функція `result()` приймає `**kw`, тож нове поле
додається без правок обгортки:

```python
# ── середня чудова, один клас провалений -> ВІДХИЛИТИ ──
out = handler(logs(result(f1=0.95, champion_f1=0.90,
    f1_per_class={"setosa": 1.0, "versicolor": 0.93, "virginica": 0.41})), None)
assert out["promote"] is False and "virginica" in out["reason"], out

# ── старий образ без поля -> перевірка мовчки пропускається ──
out = handler(logs(result(f1=0.95, champion_f1=0.90)), None)
assert out["promote"] is True, out
```

```bash
cd lambdas/evaluate && python3 test_handler.py
make pipeline-up
```

### 10б. Друга половина в тренуванні (~35 хв)

Тепер навчіть `train.py` це поле друкувати. `apps/trainer/train.py`, у
`train_one()` поруч із рештою метрик:

```python
        # average=None -> масив по класах у порядку sorted(унікальні мітки)
        per_class = f1_score(y_test, y_pred, average=None)
        f1_per_class = {name: float(v) for name, v in zip(class_names, per_class)}
        mlflow.log_metrics({f"f1_{k}": v for k, v in f1_per_class.items()})
```

повернути його з `train_one` (`return {..., "f1_per_class": f1_per_class}`) і
додати в **останній** рядок `main()`:

```python
    log(event="training_result", model=MODEL_NAME, version=version,
        run_id=best["run_id"], f1=best["f1"], accuracy=best["accuracy"],
        f1_per_class=best["f1_per_class"],
        champion_f1=current, promoted=PROMOTE_TO_CHAMPION)
```

Далі — новий образ. **Тег `v3`, не перезапис `v2`**: мутабельні теги —
граблі №6, под із `IfNotPresent` спокійно піднімає старий шар з кешу ноди і ви
годину шукаєте, чому правка не діє.

```bash
REG=$(aws sts get-caller-identity --query Account --output text).dkr.ecr.eu-central-1.amazonaws.com
aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin $REG

# Контекст — КОРІНЬ репозиторію: Dockerfile копіює і з apps/trainer/, і з apps/drift-exporter/
docker buildx build --platform linux/amd64 -f apps/trainer/Dockerfile \
  -t $REG/mds06-mlflow-tools:v3 --push .        # 8-12 хв через QEMU

# ручний прогін на новому образі
TRAINER_IMAGE=$REG/mds06-mlflow-tools:v3 make train

# пайплайн: `make pipeline-up` змінних не передає, тому один раз явно
cd terraform/training-pipeline && terraform apply -var "trainer_image=$REG/mds06-mlflow-tools:v3"
cd - && make pipeline-run N=10 D=1
```

### Що має вийти
- У логах Job зʼявляється `"f1_per_class": {"setosa": 1.0, ...}`, у MLflow —
  три нові метрики `f1_setosa` / `f1_versicolor` / `f1_virginica`, які видно в
  таблиці порівняння запусків.
- `make pipeline-run N=10 D=1` (десять дерев глибиною 1) відхиляється **з
  іменем конкретного класу** в причині, а не просто «гірше за чинну».
- До перезбірки образу пайплайн працює як раніше — саме тому перевірка
  написана як «поля немає → пропускаємо».

### На що звернути увагу
- **Порядок класів у `average=None` — це `sorted(unique(y))`, а не порядок
  появи.** Zip з `class_names` збігається лише тому, що `load_iris()` дає
  `target` як 0/1/2 і `target_names` у тому самому порядку. На своїх даних це
  треба перевіряти: `f1_score(..., labels=...)` існує саме для цього.
- **Сумісність уперед — не ввічливість, а вимога.** Gate і тренувальний образ
  оновлюються різними людьми в різний час. Перевірка, яка падає від
  відсутнього поля, перетворює оновлення gate на скоординований реліз.
- **Поріг 0.8 на клас проти 0.9 на macro — навмисно нижчий.** Найгірший клас
  завжди гірший за середнє; однаковий поріг зробив би підлогу з вправи 9
  недосяжною.
- Порівняйте два числа з логів: `f1` (macro) і `min(f1_per_class)`. Різниця між
  ними і є те, що ховає усереднення.

---

## Вправа 11. Змусити пайплайн впасти дешево ⭐ (~15 хв)

### Мета
Заміряти власним секундоміром, скільки коштує перевірка на початку і скільки —
та сама помилка, знайдена в кінці.

### Що робити

**Крок 1 — три способи впасти на 200 мс:**

```bash
make pipeline-run N=сто                     # не число
make pipeline-run N=5000                    # > MAX_ESTIMATORS = 2000
make pipeline-run N=50,100,200 D=1,2,3,4,5  # сітка 3x5 = 15 > MAX_GRID = 12
```

Кожен дає `ParamsRejected` — і це **`Fail`**, на відміну від `ModelRejected`.
Засічіть час від запуску до червоного стану. Потім у консолі AWS відкрийте граф
і подивіться на `ValidateParams` → **Input/Output**: там текст вашої помилки.

**Крок 2 — знайти дірку в перевірці.** `MAX_GRID = 12` рахує **кількість
запусків**, а не роботу:

```bash
make pipeline-run N=2000,2000 D=1,2,3    # 2x3 = 6 запусків, перевірку пройдено
```

Шість запусків по 2000 дерев — це вчетверо довше за дозволені дванадцять по 50.
Перевірка формально виконана, заняття зупинилось.

**Крок 3 — закрити її.** `lambdas/validate/handler.py`:

```python
# MAX_GRID рахує запуски, а не роботу: 2 x 3 сітки по 2000 дерев проходить
# ліміт «12 запусків» і при цьому тренується вчетверо довше за дозволений
# максимум. Обмежуємо сумарну кількість дерев.
MAX_TREES_TOTAL = 6000
```

і поруч із перевіркою `total > MAX_GRID`:

```python
    trees = sum(n_estimators) * len(depths)
    if trees > MAX_TREES_TOTAL:
        raise ValueError(
            f"сумарно {trees} дерев (сітка {n_estimators} x {len(depths)} глибин), "
            f"дозволено до {MAX_TREES_TOTAL}"
        )
```

**Крок 4 — перевірити локально, без AWS.** `handler()` навмисно переживає
`context=None` (рядок `uniq = context.aws_request_id[:8] if context is not None else "local"`):

```bash
cd lambdas/validate && python3 -c "
from handler import handler
print(handler({'commit_sha':'abc1234','n_estimators':'50,100,200','max_depth':'2,none'}, None))
for bad in [{'n_estimators':'2000,2000','max_depth':'1,2,3'},   # ваша нова межа
            {'n_estimators':'сто'}, {'n_estimators':'5000'},
            {'n_estimators':'50,100,200','max_depth':'1,2,3,4,5'}]:
    try:
        handler({'commit_sha':'abc1234', **bad}, None)
        print('❌ ПРОПУСТИЛО:', bad)
    except ValueError as e:
        print('✅ відхилено:', e)
"
```

```bash
make pipeline-up
make pipeline-run N=2000,2000 D=1,2,3        # тепер ParamsRejected
make pipeline-run                             # контроль: звичайна сітка проходить
```

### Що має вийти
Усі чотири сміттєві входи дають `ParamsRejected` за ~200 мс проти ~2-4 хвилин,
які той самий прогін жив би до падіння в поді. Звичайна сітка проходить — це
контроль, без нього ви не знаєте, чи не заблокували все підряд.

### На що звернути увагу
- **Найдешевша перевірка йде першою** — головна теза кроку 1 і причина, чому
  `ValidateParams` не всередині тренувального пода.
- **`ParamsRejected` — це `Fail`, а `ModelRejected` — `Succeed`, і це не
  непослідовність.** Сміття в параметрах = зламаний виклик, його має побачити
  людина. Відхилена модель = пайплайн спрацював як мав. Якби обидва були
  червоними, за два тижні команда перестала б дивитись на червоне взагалі.
- **Межі в `validate` свідомо широкі.** Задача — відсікти сміття, а не
  нав'язати «правильні» гіперпараметри. Занадто вузька перевірка перетворює
  gate на перешкоду експерименту, і люди починають ходити повз пайплайн.
- Ваша нова межа теж має дірку: 12 запусків по 500 дерев — це 6000, рівно
  ліміт, і це все ще довго. Знайдіть її самі й вирішіть, чи варто закривати.
  **Не кожну дірку треба латати** — інколи чесніше поставити таймаут.

---

## Вправа 12. Додати Map у state machine ⭐⭐⭐⭐ (~50 хв)

### Мета
Слайд 24 показує `Map` і `Wait` як штатні стани. Зробити їх своїми руками і
наступити на те, чого зі слайда не видно: обмеження кластера і форму даних на
виході.

### 12а. Розминка: Wait (~10 хв)

`terraform/training-pipeline/state_machine.asl.json`. Після `PromoteModel`
вставте стан і перенаправте на нього:

```json
    "WaitForReload": {
      "Type": "Wait",
      "Seconds": 30,
      "Comment": "POST /reload не блокуючий, а сервіс перечитує реєстр не миттєво. Без цієї паузи CloudWatch фіксує Promoted раніше, ніж прод справді віддає нову версію.",
      "Next": "LogMetrics"
    },
```

у `PromoteModel` замінити `"Next": "LogMetrics"` на `"Next": "WaitForReload"`.

```bash
make pipeline-up
make pipeline-run
```

У графі виконання зʼявляється сьомий стан. Заміряйте загальний час — він виріс
рівно на 30 секунд, і це ціна чесності метрики `Promoted`.

### 12б. Map: тренувати кожну глибину окремим Job (~40 хв)

Зараз одна глибина від іншої відрізняється лише рядком у сітці всередині
одного пода. Розкладемо їх на окремі Job — щоб падіння однієї конфігурації не
забирало решту.

**Крок 1.** `lambdas/validate/handler.py` уже має готовий список `depths`.
Віддайте його одним рядком у `return`:

```python
        "depth_list": depths,        # ["2", "none"] — ItemsPath для Map
```

**Крок 2.** У ASL обгорніть `TrainOnEKS` у `Map`:

```json
    "TrainGrids": {
      "Type": "Map",
      "ItemsPath": "$.params.depth_list",
      "MaxConcurrency": 1,
      "ItemSelector": {
        "params.$": "$.params",
        "depth.$": "$$.Map.Item.Value",
        "index.$": "$$.Map.Item.Index"
      },
      "ItemProcessor": {
        "ProcessorConfig": { "Mode": "INLINE" },
        "StartAt": "TrainOnEKS",
        "States": { "TrainOnEKS": { ... сюди наявний стан ... } }
      },
      "ResultPath": "$.training",
      "Next": "EvaluateModel"
    },
```

Усередині `TrainOnEKS` два рядки мусять змінитись:

```json
"name.$": "States.Format('{}-{}', $.params.job_name, $.index)",
{ "name": "GRID_MAX_DEPTH", "value.$": "$.depth" }
```

**Крок 3 — найважче, і воно не в Map.** Вихід `Map` — **масив**, а
`PromoteOrReject` уміє дивитись лише на одне поле `$.evaluation.promote`.
Два чесних виходи:

- **лінивий:** перенести `EvaluateModel` УСЕРЕДИНУ `Map` (він і так чиста
  функція) і промоутити лише останню ітерацію. Працює за 10 хвилин, але
  «найкраща з трьох» перетворюється на «остання з трьох» — і це треба сказати
  вголос, а не сховати;
- **правильний:** четверта Lambda `pick_best` — десять рядків
  `max(results, key=lambda r: r["f1"])`. Проводка: тека `lambdas/pick_best/`
  з `handler.py`, один запис у мапі `local.lambdas` (`lambdas.tf`) — ZIP, роль,
  лог-група і ARN зберуться самі, — і стан `Task` між `Map` і `PromoteOrReject`.

Оберіть один, зробіть і поясніть чому.

```bash
make pipeline-up
make pipeline-run N=100 D=2,4,none      # три ітерації Map
```

### Що має вийти
У графі виконання `TrainGrids` розгортається в три ітерації, кожна зі своїм
Job. `kubectl -n mlflow get jobs` під час прогону показує імена з суфіксами
`-0`, `-1`, `-2`, і **не більше одного Running одночасно**. У MLflow — три
окремі набори запусків.

### На що звернути увагу
- **`MaxConcurrency: 1` тут обовʼязковий, і це не обережність.** У кластері
  34 слоти подів і нуль вільних (арифметика в `docs/09-mlflow-drift.md`). Три
  Job одночасно = два Pending, `eks:runJob.sync` чекає на них до таймауту, а
  виглядає це як «Step Functions завис». Приберіть цей рядок і подивіться —
  вправа коштує 5 хвилин і запамʼятовується назавжди.
- **`$$` — обʼєкт контексту, а не змінна стану.** `$$.Map.Item.Value` /
  `$$.Map.Item.Index` доступні ЛИШЕ всередині `ItemProcessor`. Спроба
  звернутись до них зовні дає `States.Runtime` без пояснення.
- **Імена Job мусять розходитись між ітераціями.** Без суфікса `-{index}`
  друга ітерація отримає `EKS.409 AlreadyExists`, бо перша ще не прибралась
  (`ttlSecondsAfterFinished: 1800`).
- **Що дає Map насправді.** Не швидкість (`MaxConcurrency: 1`), а **ізоляцію**:
  впала одна конфігурація — решта дотренувались, і в реєстрі є що порівнювати.
  У сітці всередині одного пода падіння на третій конфігурації забирає всі шість.
- `Wait` з 12а дорожчий, ніж здається: Standard Workflow тарифікується за
  переходи станів, а не за час, тож 30 секунд очікування безкоштовні. А от у
  Express Workflow тарифікується тривалість — і той самий `Wait` став би
  рядком у рахунку.

---

## Що здавати

| Вправа | Артефакт |
|---|---|
| 1-2 | скріншот таблиці 10 запусків + один абзац: чому різниця в accuracy між двома верхніми рядками не є доказом |
| 3 | два виводи пода `predict` — до і після перевішування аліаса — і один абзац: чому це найнебезпечніша команда в темі |
| 4 | реальний ключ артефакту з `mc ls` і `artifact_uri` з Postgres рядом |
| 5 | скріншот панелі 6 з видимим переходом 0 → 1 і час T0 |
| 6 | чотири власні вирази PromQL + скріншот порожнього результату там, де його не має бути |
| 7 | два запити LogQL і зіставлення часу: коли стрибнув Loki, коли впав p-value |
| 8 | для кожної з чотирьох поломок: що зламалось, що при цьому **брехало**, як полагодили |
| 9 | вивід `make pipeline-run N=500 D=1` до і після правки gate + диф `handler.py` і `test_handler.py` |
| 10 | рядок `training_result` з `f1_per_class` і причина відхилення з іменем класу; один абзац: що саме ховало усереднення |
| 11 | час до `ParamsRejected` за секундоміром і вивід локальної перевірки з кроку 4 |
| 12 | скріншот графа з розгорнутим `Map` + `kubectl -n mlflow get jobs` під час прогону + один абзац: який із двох виходів кроку 3 обрали і чому |

---

## Прибирання після вправ

```bash
kubectl -n mlflow delete job --all                                # якщо ttl ще не спрацював
kubectl -n mlflow delete pod gp2-dead --ignore-not-found --force
kubectl -n mlflow delete pvc gp2-dead --ignore-not-found
kubectl -n ml-demo set env deploy/load-generator DRIFT_SHIFT=0
kubectl -n ml-demo scale deploy/load-generator --replicas=0       # щоб не гнати трафік цілодобово
make clean                                                        # зупинити всі тунелі
```

Після вправ 9-12 — повернути пайплайн і чемпіона в робочий стан:

```bash
git checkout lambdas/ terraform/training-pipeline/state_machine.asl.json
make pipeline-up          # відкотити Lambda і state machine до версії з репозиторію
make train                # нормальний @champion замість підсадженого у вправі 9
make pipeline-down        # якщо пайплайн більше не потрібен: Lambda, SFN, ролі, Access Entry
```

`make pipeline-down` нічого не зберігає — увесь стан у MLflow. Кластер і стек
Тем 8-9 він не чіпає.

**Забутий PVC — це рахунок.** 8 GiB gp3 тихо тягне $0.76/міс. Перед
`terraform destroy` обовʼязково: `kubectl delete pvc --all -A`, потім
`aws ec2 describe-volumes --filters Name=status,Values=available --region eu-central-1`
мусить бути порожнім.
