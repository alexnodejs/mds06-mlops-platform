"""Тренування Iris RandomForest із повним логуванням у MLflow (слайди 29-33).

Та сама модель, що в Темі 8 (apps/model-api/train.py): ті самі
load_iris, train_test_split(test_size=0.2, random_state=42, stratify=y) і
RandomForestClassifier. Змінилося ОДНЕ: результат більше не зникає разом із
подом — параметри, метрики, графік і сама модель ідуть у MLflow
(PostgreSQL — параметри й метрики, MinIO — артефакти).

Запуск у кластері (Job):
    envFrom: [{secretRef: {name: mlflow-credentials}}]
    command: ["python", "train_mlflow.py"]
Секрет mlflow-credentials уже містить усі 5 потрібних змінних:
MLFLOW_TRACKING_URI, MLFLOW_S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION.

Локально, без жодного сервера (саме так цей файл і перевірявся):
    MLFLOW_TRACKING_URI=file:///tmp/mlruns python train_mlflow.py
"""

import io
import itertools
import json
import os
import tempfile
from pathlib import Path

import boto3
import matplotlib

# Agg — рендер у файл без графічного дисплея. БЕЗ цього рядка у контейнері
# matplotlib шукає Tk/Qt і падає на імпорті pyplot. Ставити ДО pyplot.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402  (порядок навмисний, див. вище)
import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow.models import infer_signature
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split

# ── Налаштування ──────────────────────────────────────────────────────────
# MLFLOW_TRACKING_URI mlflow читає з env САМ — set_tracking_uri у коді не
# потрібен. Так той самий образ працює і в кластері, і на ноутбуці.
EXPERIMENT = os.getenv("MLFLOW_EXPERIMENT_NAME", "iris-rf")
MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", "iris-rf")
TEST_SIZE = float(os.getenv("TEST_SIZE", "0.2"))
RANDOM_STATE = int(os.getenv("RANDOM_STATE", "42"))

# Імена ознак — snake_case, рівно як у POST /predict Теми 8 і в дріфт-експортері.
# У sklearn вони "sepal length (cm)"; беремо свої, щоб signature моделі
# збігалася з контрактом API, а не з внутрішнім іменуванням датасету.
FEATURES = ["sepal_length", "sepal_width", "petal_length", "petal_width"]

# ⭐ Тема 11: дані приходять ЗІ СХОВИЩА, а не з пакета sklearn.
# Порожній рядок = працюємо на вбудованому load_iris(); саме так цей файл
# лишається запускним без кластера:
#     MLFLOW_TRACKING_URI=file:///tmp/mlruns DATASET_URI= python train.py
DATASET_URI = os.getenv("DATASET_URI", "s3://datasets/iris/v2.csv")

# Хто і з якого коду тренував — теги версії моделі (слайди 16 і 22).
# Порожні значення краще за вигадані: «невідомо» чесніше, ніж «localhost».
GIT_SHA = os.getenv("GIT_SHA", "")
TRAINED_BY = os.getenv("TRAINED_BY", "")

# Сітка гіперпараметрів: 3 x 2 = 6 запусків. Саме щоб у MLflow UI було ЩО
# порівнювати (слайди 31-33): max_depth=2 навмисно недовчений, тож у таблиці
# видно розкид метрик, а не шість однакових рядків.
GRID_N_ESTIMATORS = [int(x) for x in os.getenv("GRID_N_ESTIMATORS", "50,100,200").split(",")]
GRID_MAX_DEPTH = [None if x in ("", "none", "None") else int(x)
                  for x in os.getenv("GRID_MAX_DEPTH", "2,none").split(",")]

# Прапорець «одразу в прод». Порівняння саме з "true": будь-яке інше значення
# (false, 0, порожній рядок, друкарська помилка) означає НЕ промоутити —
# безпечний бік за замовчуванням для автоматичного пайплайну.
PROMOTE_TO_CHAMPION = os.getenv("PROMOTE_TO_CHAMPION", "true").strip().lower() == "true"


def log(**fields) -> None:
    """JSON-лог у stdout — той самий формат, що в app.py Теми 8, тож логи
    Job-а читаються в Loki тим самим запитом."""
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)


def _read_s3_csv(uri: str) -> pd.DataFrame:
    """Читає s3://bucket/key у DataFrame.

    🔴 endpoint_url ПЕРЕДАЄМО ЯВНО. MLFLOW_S3_ENDPOINT_URL — змінна MLflow, а не
    botocore: MLflow читає її сам і передає в boto3 параметром. `boto3.client("s3")`
    без endpoint_url піде у СПРАВЖНІЙ AWS S3 із ключами `minioadmin`, отримає 403,
    і виглядатиме це як «зламався MinIO».
    """
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    s3 = boto3.client("s3", endpoint_url=os.getenv("MLFLOW_S3_ENDPOINT_URL") or None)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pd.read_csv(io.BytesIO(body))


def load_dataset():
    """Повертає (X, y, class_names, dataset) — де dataset це обʼєкт MLflow або None.

    Фолбек на load_iris() свідомий: він рятує локальний прогін без кластера і
    перший запуск, коли сейдинг ще не відпрацював. Але фолбек ГУЧНИЙ — подія в
    лозі, — бо мовчазний відкат на інші дані означав би, що модель навчена не на
    тому, що написано в її тегах.
    """
    if DATASET_URI:
        try:
            df = _read_s3_csv(DATASET_URI)
            class_names = sorted(df["target"].unique())
            X = df[FEATURES]
            # Мітки в CSV — рядки ("setosa"), а моделі потрібні індекси.
            # Порядок класів беремо відсортований, тобто той самий, що в
            # load_iris(), — інакше номер класу означав би різне в різних
            # запусках, і матриця невідповідностей стала б нечитабельною.
            y = df["target"].map({c: i for i, c in enumerate(class_names)}).to_numpy()
            dataset = mlflow.data.from_pandas(df, source=DATASET_URI, name="iris",
                                              targets="target")
            log(event="dataset_loaded", uri=DATASET_URI, rows=len(df),
                classes=class_names, digest=dataset.digest)
            return X, y, [str(c) for c in class_names], dataset
        except Exception as e:  # noqa: BLE001
            log(event="dataset_unavailable", uri=DATASET_URI, error=str(e),
                hint="падаю на вбудований load_iris(); запустіть `make seed`")

    data = load_iris()
    X = pd.DataFrame(data.data, columns=FEATURES)
    log(event="dataset_builtin", rows=len(X), hint="дані з пакета sklearn, не зі сховища")
    return X, data.target, [str(n) for n in data.target_names], None


def confusion_png(y_test, y_pred, class_names, path: Path) -> None:
    """Матриця невідповідностей у PNG — артефакт для log_artifact (слайд 29).

    Метрика говорить «f1 = 0.93», а матриця показує, ЯКІ саме класи модель
    плутає. Для Iris це завжди versicolor <-> virginica.
    """
    fig, ax = plt.subplots(figsize=(4.5, 4), dpi=130)
    ConfusionMatrixDisplay.from_predictions(
        y_test, y_pred, display_labels=class_names, cmap="Blues", colorbar=False, ax=ax
    )
    ax.set_xlabel("Передбачений клас")
    ax.set_ylabel("Справжній клас")
    ax.set_title("Матриця невідповідностей")
    fig.tight_layout()
    fig.savefig(path)
    # close ОБОВ'ЯЗКОВО: у циклі по сітці незакриті фігури накопичуються в
    # пам'яті процесу, і matplotlib починає сипати попередженнями про 20+ figures.
    plt.close(fig)


def train_one(params, class_names, split, tmp: Path, dataset=None):
    """Один запуск: mlflow.start_run() -> параметри -> метрики -> артефакти -> модель."""
    X_train, X_test, y_train, y_test = split

    with mlflow.start_run(run_name=f"rf_n{params['n_estimators']}_d{params['max_depth']}") as run:
        # ── log_input: НА ЯКИХ ДАНИХ (слайд 16) ───────────────────────────
        # Рідний Dataset API MLflow, а не саморобний тег. Він рахує digest —
        # хеш вмісту — і зберігає схему та профіль. Саме digest відповідає на
        # питання «це той самий файл, чи його підмінили»: імена й шляхи брешуть,
        # хеш — ні. У MLflow UI це окрема вкладка Datasets у запуску.
        if dataset is not None:
            mlflow.log_input(dataset, context="training")

        # ── log_param: ЩО ми налаштували (слайд 29) ───────────────────────
        # Гіперпараметри моделі + параметри розбиття. Без test_size і
        # random_state два запуски з однаковим n_estimators були б
        # непорівнянні, а таблиця в UI — брехливою.
        mlflow.log_params({
            "n_estimators": params["n_estimators"],
            "max_depth": params["max_depth"],
            "test_size": TEST_SIZE,
            "random_state": RANDOM_STATE,
        })

        clf = RandomForestClassifier(random_state=RANDOM_STATE, **params)
        clf.fit(X_train, y_train)
        y_pred = clf.predict(X_test)

        # ── log_metric: ЩО вийшло (слайд 29) ──────────────────────────────
        # average="macro" — середнє по трьох класах з РІВНОЮ вагою. Для Iris
        # класи збалансовані, тож macro і weighted тут майже однакові, але
        # macro чесніший за замовчуванням: він не дає великому класу
        # замаскувати провал на малому.
        metrics = {
            "accuracy": float(accuracy_score(y_test, y_pred)),
            "precision": float(precision_score(y_test, y_pred, average="macro")),
            "recall": float(recall_score(y_test, y_pred, average="macro")),
            "f1": float(f1_score(y_test, y_pred, average="macro")),
        }
        mlflow.log_metrics(metrics)

        # ── log_artifact: файли (слайд 29) ────────────────────────────────
        png = tmp / "confusion_matrix.png"
        confusion_png(y_test, y_pred, class_names, png)
        mlflow.log_artifact(str(png))

        # Еталонний набір поруч із моделлю: дріфт-експортер порівнює поточні
        # дані САМЕ з цими рядками. Зараз він бере їх із sklearn у себе в
        # образі (детерміновано, без мережі), але артефакт логуємо — щоб
        # лінія «модель <-> дані, на яких вона вчилась» була видима в UI.
        ref = X_train.copy()
        ref["target"] = [class_names[i] for i in y_train]
        ref_csv = tmp / "reference.csv"
        ref.to_csv(ref_csv, index=False)
        mlflow.log_artifact(str(ref_csv))

        # ── log_model: сама модель + signature + input_example (слайд 30) ──
        # signature — схема входу/виходу. Без неї MLflow приймає будь-який
        # DataFrame і падає вже в рантаймі; з нею невідповідність колонок
        # ловиться на межі моделі.
        # input_example — 2 реальні рядки. Крім документації в UI, MLflow на
        # них ПРОГАНЯЄ predict під час логування, тож зламану модель видно
        # відразу, а не при першому запиті користувача.
        info = mlflow.sklearn.log_model(
            clf,
            name="model",
            signature=infer_signature(X_test, y_pred),
            input_example=X_test.head(2),
        )

        log(event="run_finished", run_id=run.info.run_id, params=params, **metrics)
        return {"run_id": run.info.run_id, "uri": info.model_uri, **metrics}


def champion_f1():
    """f1 моделі, яка ЗАРАЗ у проді (аліас @champion), або None.

    Це друга половина контракту з пайплайном Теми 10. Quality gate — Lambda
    ЗЗОВНІ кластера, а MLflow тут за ClusterIP і ззовні недоступний. Тому
    число «з чим порівнювати» дістає той, хто вже має доступ, — цей под, — і
    друкує його разом із результатом.

    Викликати ОБОВʼЯЗКОВО до register_best: при PROMOTE_TO_CHAMPION=true аліас
    переїде на нову версію, і ми порівняли б модель саму з собою.
    """
    try:
        client = mlflow.MlflowClient()
        mv = client.get_model_version_by_alias(MODEL_NAME, "champion")
        return float(client.get_run(mv.run_id).data.metrics["f1"])
    except Exception:  # noqa: BLE001
        # Немає моделі, немає аліаса, немає реєстру (file:// бекенд), немає
        # метрики f1 у тому запуску — усе це один і той самий випадок:
        # порівнювати нема з чим, отже нова модель стає першою.
        return None


def register_best(best):
    """Реєстрація найкращої моделі в Model Registry (слайд 33).

    Registry — це іменована версійована полиця: код у проді просить
    "models:/iris-rf@champion" і не знає ні про run_id, ні про шлях у MinIO.

    РЕЄСТРАЦІЯ І ПРОМОЦІЯ — ДВІ РІЗНІ ДІЇ, і саме тому вони розділені:

      register_model      завжди -> зʼявляється нова версія (v5, v6, ...)
      set_..._alias       за прапорцем PROMOTE_TO_CHAMPION

    Ручний запуск (Тема 9, `make train`) ставить прапорець у true: студент
    хоче побачити модель у проді одразу.

    Пайплайн Теми 10 ставить false. Там рішення «краща вона чи ні» ухвалює
    quality gate у Step Functions ПІСЛЯ тренування, порівнявши метрики з
    чинним @champion. Якби train.py вішав аліас сам, gate не мав би що
    вирішувати: гірша модель уже була б у проді.
    """
    version = mlflow.register_model(best["uri"], MODEL_NAME).version
    client = mlflow.MlflowClient()

    # ⭐ ТЕМА 11, СЛАЙД 16: модель — не ізольований файл.
    # Без цих тегів версія відповідає лише на питання «яка вона за рахунком».
    # З ними — на всі чотири зі слайда 7: звідки взялась, хто натренував, на
    # яких даних, з якими параметрами.
    #
    # Теги вішаємо на ВЕРСІЮ, а не на запуск: запусків у сітці шість, а в прод
    # їде один. Питання «на яких даних працює те, що зараз обслуговує клієнтів»
    # має отримати відповідь за один клік у реєстрі, без переходу в експерименти.
    tags = {
        "git_sha": GIT_SHA or "unknown",
        "dataset": DATASET_URI or "sklearn:load_iris",
        "dataset_digest": best.get("dataset_digest") or "unknown",
        "trained_by": TRAINED_BY or "manual",
        # Слайди 14-15 говорять про Draft/Staging/Production. У MLflow 3.x
        # stage-и застарілі, тож статус тримаємо тегом, а «де воно зараз
        # працює» — аліасом. Мапу одне в одне розписано в docs/11.
        "status": "draft",
    }
    for key, value in tags.items():
        client.set_model_version_tag(MODEL_NAME, version, key, value)

    # Опис — те, що людина читає ПЕРШИМ, відкривши версію в UI. Тому тут
    # не дублювання тегів, а вичавка: на чому вчилась, що вийшло, з якого коду.
    client.update_model_version(
        MODEL_NAME, version,
        description=(
            f"f1={best['f1']:.4f}, accuracy={best['accuracy']:.4f}\n"
            f"дані: {DATASET_URI or 'sklearn:load_iris'}\n"
            f"код: {GIT_SHA[:12] or 'невідомо'}\n"
            f"хто: {TRAINED_BY or 'ручний запуск'}"
        ),
    )

    if not PROMOTE_TO_CHAMPION:
        # Слайд 15, Staging: модель пройшла тренування, але не перевірку.
        # Аліас @challenger — це і є «кандидат»: саме його бере green у
        # blue-green деплої (Тема 11) і саме його оцінює quality gate (Тема 10).
        client.set_registered_model_alias(MODEL_NAME, "challenger", version)
        client.set_model_version_tag(MODEL_NAME, version, "status", "staging")
        log(event="registered", model=MODEL_NAME, version=version, alias="challenger",
            hint="кандидат; у прод його пустить лише quality gate")
        return version

    # Alias замість давніх stage (Staging/Production): stage у MLflow 3.x
    # вважаються застарілими, alias можна перевісити на іншу версію одним
    # викликом, і посилання "models:/iris-rf@champion" у коді не змінюється.
    mlflow.MlflowClient().set_registered_model_alias(MODEL_NAME, "champion", version)
    client.set_model_version_tag(MODEL_NAME, version, "status", "production")
    log(event="registered", model=MODEL_NAME, version=version, alias="champion")
    return version


def main() -> None:
    # X — DataFrame, а не numpy: так у signature і input_example будуть ІМЕНА
    # колонок, і в MLflow UI видно, що саме модель чекає на вході.
    X, y, class_names, dataset = load_dataset()
    split = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE, stratify=y
    )

    mlflow.set_experiment(EXPERIMENT)
    log(event="training_started", tracking_uri=mlflow.get_tracking_uri(),
        experiment=EXPERIMENT, runs=len(GRID_N_ESTIMATORS) * len(GRID_MAX_DEPTH))

    results = []
    # tempfile, а не поточна тека: контейнер працює під uid 1000, /app
    # належить root, тож запис у cwd впаде з Permission denied.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for n, depth in itertools.product(GRID_N_ESTIMATORS, GRID_MAX_DEPTH):
            results.append(train_one({"n_estimators": n, "max_depth": depth},
                                     class_names, split, tmp, dataset))

    # Найкращий — за f1 (macro). При однаковому f1 tie-break за accuracy:
    # на Iris кілька конфігурацій регулярно дають однакові метрики, і без
    # tie-break "найкращий" залежав би від порядку в сітці.
    best = max(results, key=lambda r: (r["f1"], r["accuracy"]))
    # Digest датасету їде разом із переможцем: теги вішаються на ВЕРСІЮ моделі,
    # а версія створюється нижче, коли обʼєкта dataset у тій функції вже немає.
    best["dataset_digest"] = dataset.digest if dataset is not None else None
    log(event="best_run", run_id=best["run_id"], f1=best["f1"], accuracy=best["accuracy"])

    # Єдиний випадок, коли реєстрацію МОЖНА мовчки пропустити, — файловий
    # бекенд: FileStore не має Model Registry в принципі ("Model Registry
    # features are not supported with file store"). Це НЕ помилка коду, тому
    # перевіряємо його ЯВНО, за схемою URI; голий шлях без "://" (./mlruns) —
    # той самий FileStore. У кластері MLflow стоїть на PostgreSQL — саме тому
    # слайд 22 вимагає базу.
    # Широкого except Exception тут БУТИ НЕ МОЖЕ: лежачий MinIO, протухлий
    # пароль чи 500 від MLflow лишали б Job ЗЕЛЕНИМ без моделі в реєстрі, а
    # крок EvaluateModel Теми 10 чекав би на подію training_result, якої не
    # буде. Тихий зелений Job — найдорожчий вид падіння.
    uri = mlflow.get_tracking_uri()
    if uri.startswith("file:") or "://" not in uri:
        log(event="registry_unavailable", tracking_uri=uri,
            hint="Model Registry потребує БД-бекенд; при file:// цей крок пропускається")
        return

    # ⚠️ ПОРЯДОК ВАЖЛИВИЙ: спершу дізнаємось чинного чемпіона, потім
    # реєструємо нову версію. Навпаки — і при PROMOTE_TO_CHAMPION=true
    # ми прочитали б уже нову модель і порівняли її саму з собою.
    current = champion_f1()
    version = register_best(best)
    # ⭐ ФІНАЛЬНИЙ РЯДОК КОНТРАКТУ з пайплайном Теми 10.
    # Крок EvaluateModel у Step Functions читає логи цього пода і шукає
    # рівно цю подію. Не перейменовувати поля, не міняти порядок виводу:
    # ключі парсяться за іменами, але рядок мусить бути ОСТАННІМ, бо
    # парсер бере останнє входження.
    log(event="training_result", model=MODEL_NAME, version=version,
        run_id=best["run_id"], f1=best["f1"], accuracy=best["accuracy"],
        champion_f1=current, promoted=PROMOTE_TO_CHAMPION)


if __name__ == "__main__":
    main()
