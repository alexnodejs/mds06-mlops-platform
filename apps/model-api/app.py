"""ML-сервіс на FastAPI: класифікація Iris + метрики Prometheus + JSON-логи.

Ендпоїнти: GET / (довідка), POST /predict, GET /healthz, GET /metrics.
Модель лежить у образі (натренована на етапі docker build) — у рантаймі
жодних походів у мережу і жодних томів.
"""

import json
import os
import pickle
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel

# ─────────────────────────────────────────────────────────────
# Логування
# ─────────────────────────────────────────────────────────────
# Один JSON-обʼєкт на рядок (JSON Lines) у stdout. Навіщо саме так:
# у Loki є вбудований парсер `| json`, який робить поля фільтрованими
# ЧАСУ ЗАПИТУ — {app="ml-model"} | json | confidence < 0.7 працює одразу.
# З текстовим логом довелось би писати regexp, що ламається від першої ж
# зміни формулювання. Числа при цьому лишаються числами, тож числові
# порівняння в LogQL справді числові.
# УВАГА: stdlib json + print, БЕЗ structlog — додавати залежність заради
# форматування рядка тут дорожче за саму задачу.
def log(**fields) -> None:
    # ts першим полем — так рядок читабельний навіть без jq.
    record = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")}
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), flush=True)


# ─────────────────────────────────────────────────────────────
# Модель
# ─────────────────────────────────────────────────────────────
MODEL_PATH = Path(os.getenv("MODEL_PATH", Path(__file__).with_name("model.pkl")))
APP_VERSION = os.getenv("APP_VERSION", "v1")

# ═══════════════════════════════════════════════════════════════════════════
# ЗВІДКИ БЕРЕТЬСЯ МОДЕЛЬ
# ═══════════════════════════════════════════════════════════════════════════
# Два джерела, і це навмисно:
#
#   1) РЕЄСТР MLflow (models:/iris-rf@champion) — якщо задано MLFLOW_TRACKING_URI.
#      Тоді promote у MLflow == деплой: змінили аліас champion -> сервіс
#      підхопить нову версію без перезбірки образу.
#
#   2) model.pkl, зашитий в образ — резерв. Якщо MLflow недоступний (упав,
#      ще не піднявся, або ви взагалі проходите лише Тему 8), сервіс
#      стартує на ньому. Без цього резерву падіння MLflow клало б інференс,
#      а це рівно та зв'язність, якої в проді уникають.
#
# Стан тримаємо в одному словнику, щоб міняти його атомарно: під час
# перезавантаження запити продовжують обслуговуватись старою моделлю.
with MODEL_PATH.open("rb") as f:
    BUNDLE = pickle.load(f)

MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "").strip()
MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", "iris-rf")
MODEL_ALIAS = os.getenv("MLFLOW_MODEL_ALIAS", "champion")
RELOAD_SECONDS = int(os.getenv("MODEL_RELOAD_SECONDS", "30"))

STATE = {
    "model": BUNDLE["model"],
    "classes": BUNDLE["classes"],
    "source": "baked",          # baked | registry
    "version": APP_VERSION,     # для registry — номер версії моделі
    "model_type": BUNDLE["model_type"],
    "meta": {"accuracy": BUNDLE.get("accuracy"), "f1": None,
             "params": {}, "run_id": None},
}
_LOCK = threading.Lock()


_UNCHANGED = object()  # sentinel: аліас указує на ту саму версію, качати нічого


def _fetch_from_registry(current_version=None):
    """Тягне модель за аліасом. Повертає кортеж, None або _UNCHANGED."""
    if not MLFLOW_URI:
        return None
    import mlflow  # імпорт усередині: без MLFLOW_TRACKING_URI пакет навіть не потрібен

    mlflow.set_tracking_uri(MLFLOW_URI)
    client = mlflow.MlflowClient()
    mv = client.get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
    # Порівняння версій СТОЇТЬ ПЕРЕД load_model і це принципово: аліас читається
    # одним дешевим викликом до трекінг-сервера, а load_model тягне артефакт з
    # MinIO цілком. Поллер ходить раз на 30 с -> 2880 завантажень на добу, з
    # яких 2879 віддають байт-у-байт ту саму модель. Тут — ранній вихід.
    if current_version is not None and str(mv.version) == current_version:
        return _UNCHANGED
    # sklearn-флейвор, а НЕ pyfunc: нам потрібен predict_proba для метрики
    # впевненості, а pyfunc.predict() віддає лише клас. Плюс pyfunc вимагає
    # DataFrame з іменами колонок, тобто ще й pandas у залежностях.
    model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@{MODEL_ALIAS}")
    classes = list(BUNDLE["classes"])  # порядок класів Iris незмінний

    # Метрики й гіперпараметри того запуску, з якого зроблено цю версію.
    # Потрібні, щоб на сторінці показувати ПРАВДУ про модель, яка зараз
    # обслуговує, а не характеристики зашитої в образ.
    meta = {}
    try:
        run = client.get_run(mv.run_id)
        meta = {"accuracy": run.data.metrics.get("accuracy"),
                "f1": run.data.metrics.get("f1"),
                "params": dict(run.data.params),
                "run_id": mv.run_id[:8]}
    except Exception:  # noqa: BLE001 — метадані приємні, але не критичні
        pass

    # ⭐ ТЕМА 11: теги й опис версії. Доти сервіс читав лише номер версії — і на
    # питання слайда 7 «на яких даних працює те, що зараз обслуговує клієнтів»
    # відповіді не існувало ніде, крім голови того, хто запускав тренування.
    #
    # Теги живуть на ВЕРСІЇ, а не на запуску: запусків у сітці шість, у прод
    # їде один. Тому це окреме читання, а не поле з run.data.
    meta["tags"] = dict(getattr(mv, "tags", {}) or {})
    meta["description"] = (getattr(mv, "description", "") or "").strip()
    # Аліаси показують, ЯКУ РОЛЬ ця версія грає зараз: champion, challenger,
    # previous. Одна версія може мати кілька — і це нормально.
    meta["aliases"] = list(getattr(mv, "aliases", []) or [])
    return model, classes, str(mv.version), type(model).__name__, meta


def reload_model(reason="manual"):
    """Перезавантажує модель з реєстру. Тихо лишає стару, якщо не вийшло."""
    global STATE
    try:
        state = STATE  # одне читання посилання, далі працюємо з ним
        current = state["version"] if state["source"] == "registry" else None
        got = _fetch_from_registry(current)
        if got is None:
            return False, "MLFLOW_TRACKING_URI не задано"
        if got is _UNCHANGED:
            return False, f"версія {current} вже завантажена"
        model, classes, version, mtype, meta = got
        with _LOCK:
            # Одне переприсвоєння цілого словника, а НЕ .update() по ключах:
            # .update() лишає вікно, у якому predict уже взяв нову model, але
            # ще старий classes — і відповідь містить чужу назву класу.
            # Тут читач бачить або повністю стару, або повністю нову модель.
            STATE = {"model": model, "classes": classes, "source": "registry",
                     "version": version, "model_type": mtype, "meta": meta}
            # MODEL_INFO — теж під локом: .clear() + .set(1) це ДВІ операції, і
            # без лока другий reload (поллер + POST /reload одночасно) може
            # вклинитись між ними й лишити метрику або порожньою, або з двома
            # версіями одразу.
            MODEL_INFO.clear()
            MODEL_INFO.labels(version=version, model_type=mtype, source="registry").set(1)
        MODEL_RELOADS.labels(result="ok").inc()
        log(level="INFO", event="model_reloaded", version=version,
            model_type=mtype, reason=reason)
        return True, f"завантажено версію {version}"
    except Exception as exc:  # noqa: BLE001 — падіння реєстру НЕ має класти інференс
        MODEL_RELOADS.labels(result="error").inc()
        log(level="ERROR", event="model_reload_failed", error=str(exc), reason=reason)
        return False, str(exc)


def _poller():
    """Фоновий опитувач: перевіряє аліас раз на RELOAD_SECONDS.

    Спершу читає, потім спить — а не навпаки. Це перша спроба дістати модель
    з реєстру взагалі: _startup() навмисно нічого не читає, щоб не блокувати
    відкриття порту. Зі sleep на початку под перші 30 секунд віддавав би
    модель, зашиту в образ, навіть коли реєстр давно доступний.
    """
    while True:
        reload_model(reason="poll")
        time.sleep(RELOAD_SECONDS)

# ─────────────────────────────────────────────────────────────
# Метрики (імена — жорсткий контракт із дашбордом Grafana)
# ─────────────────────────────────────────────────────────────
PREDICT_REQUESTS = Counter(
    "predict_requests_total",
    "Кількість успішних передбачень",
    ["predicted_class"],
)

# Histogram, а не Gauge/Summary — і це не смак, а математика:
#  • Gauge зберігає лише ОСТАННЄ значення; між скрейпами (60 с) проходять
#    сотні запитів, і ви побачите один випадковий — викиди зникають.
#  • Summary рахує квантилі в КОЖНОМУ поді окремо, а квантилі не додаються:
#    p95 поду A + p95 поду B ≠ p95 сервісу. На 2+ репліках цифра — брехня.
#  • Histogram віддає _bucket/_sum/_count; бакети додаються між подами, тому
#    histogram_quantile(0.95, sum by (le) (rate(..._bucket[5m]))) чесний.
# Бакети ЗАМІРЯНІ, не вгадані: 441 реальний запит дав ~5.8 мс end-to-end
# (на нативному x86_64 очікувано 2-3 мс). Дефолтні бакети prometheus_client
# починаються з 0.005 — усе впало б в один стовпчик і histogram_quantile
# повертав би сміття. Тому чотири точки в зоні 1-10 мс, де живе сервіс,
# і хвіст 0.025-1.0 на випадок холодного старту / CPU throttling.
PREDICT_LATENCY = Histogram(
    "predict_latency_seconds",
    "Час обробки запиту /predict, секунди",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)

# Для 3 класів максимальна ймовірність не може бути меншою за 1/3, тож
# бакети нижче 0.4 мертві. Вище 0.9 крок дрібнішає (0.95, 0.99, 1.0), бо
# саме там скупчується маса: RandomForest на чистому Iris дає рівно 1.0
# у 74% випадків. 0.99 і 1.0 — фізично різні стани (99 дерев проти 100).
PREDICT_CONFIDENCE = Histogram(
    "predict_confidence",
    "Розподіл впевненості моделі",
    ["predicted_class"],
    buckets=(0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1),
)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Усі HTTP-запити до сервісу",
    ["method", "path", "status"],
)

# Gauge=1 з лейблами — класичний прийом "info metric": саме значення нікому
# не потрібне, уся інформація живе в лейблах і приєднується до інших метрик
# через group_left. Дає змогу побачити на дашборді, яка версія зараз у поді.
# Лейбл source показує на дашборді, звідки взято модель: baked (з образу)
# чи registry (з MLflow). Під час демо promote видно, як він перемикається.
MODEL_INFO = Gauge("model_info", "Інформація про завантажену модель",
                   ["version", "model_type", "source"])
MODEL_INFO.labels(version=APP_VERSION, model_type=BUNDLE["model_type"],
                  source="baked").set(1)

MODEL_RELOADS = Counter("model_reload_total",
                        "Спроби перезавантажити модель із реєстру", ["result"])

app = FastAPI(title="ML-сервіс Iris", version=APP_VERSION)


# ═══════════════════════════════════════════════════════════════════════════
# PROMOTE У MLFLOW == ДЕПЛОЙ
# ═══════════════════════════════════════════════════════════════════════════
# Перевісили аліас champion на іншу версію -> цей сервіс підхопить її сам,
# без перезбірки образу і без kubectl. Два шляхи:
#   • фоновий опитувач раз на MODEL_RELOAD_SECONDS (за замовчуванням 30 с)
#   • POST /reload — миттєво, щоб не чекати на занятті
@app.on_event("startup")
def _startup():
    # 🔴 ТУТ НЕ МОЖЕ БУТИ reload_model(). Це коштувало одного розгортання.
    #
    # Раніше стояв синхронний виклик, обгорнутий у try/except — і здавалось,
    # що цього досить. Але помилка тут не КИДАЄТЬСЯ, а БЛОКУЄ: коли MLflow ще
    # піднімається (а при `make up` він у хвилі 2, тобто пізніше за модель),
    # HTTP-клієнт MLflow висить на ретраях із backoff. uvicorn не завершує
    # startup, порт 8000 не відкривається, /healthz дає connection refused,
    # liveness probe валить под — і виглядає це як зламаний образ.
    #
    # Урок ширший за цей файл: try/except рятує від ВИНЯТКУ, а не від
    # ЗАВИСАННЯ. Усе, що ходить у мережу на старті, має бути асинхронним.
    #
    # Тепер под піднімається миттєво на моделі, зашитій в образ
    # (STATE["source"] == "baked"), і перемикається на реєстр, щойно MLflow
    # відповість — перша ітерація опитувача йде одразу, без паузи.
    log(level="INFO", event="startup",
        model_source=STATE["source"], model_version=STATE["version"],
        detail="перше читання реєстру — у фоні, старт не блокується")
    if MLFLOW_URI:
        # daemon=True: потік не заважає поду коректно завершитись
        threading.Thread(target=_poller, daemon=True).start()


@app.post("/reload")
def reload_endpoint():
    """Примусово перечитати аліас із реєстру."""
    ok, msg = reload_model(reason="manual")
    return {"reloaded": ok, "detail": msg,
            "version": STATE["version"], "source": STATE["source"]}

# Лейбл path беремо лише з відомого списку. Інакше будь-який сканер, що
# стукає у /wp-admin.php, породжує новий часовий ряд — це і є вибух
# кардинальності, від якого Prometheus помирає.
KNOWN_PATHS = {"/", "/predict", "/healthz", "/metrics", "/reload", "/docs", "/openapi.json"}


@app.middleware("http")
async def count_requests(request: Request, call_next):
    # Час міряємо ТУТ, а не всередині predict(). Різниця принципова:
    # всередині хендлера ми б заміряли лише predict_proba (~1.5 мс), а
    # користувач чекає ~10 мс — валідацію pydantic, серіалізацію відповіді
    # й накладні витрати HTTP. Панель зветься «Час відповіді», тож і міряти
    # має відповідь цілком, інакше графік розходиться з реальністю у 5-7 разів.
    path = request.url.path if request.url.path in KNOWN_PATHS else "other"
    start = time.perf_counter()

    # try/except обовʼязковий: незловлений виняток у хендлері НЕ повертається
    # з call_next, а летить далі — і без цього блоку рядки нижче просто не
    # виконуються. Наслідок: http_requests_total ніколи не отримує
    # status="500", і панель «Помилки» в Grafana порожня саме тоді, коли
    # помилки є. Виняток кидаємо далі — 500 клієнту віддає Starlette.
    # ⚠️ Значення ДО try, а не тільки в гілках. asyncio.CancelledError (клієнт
    # відвалився, uvicorn зупиняється) — це BaseException, його `except
    # Exception` НЕ ловить, але finally однаково виконається. Без цього рядка
    # там буде UnboundLocalError, який З'ЇСТЬ оригінальний виняток разом із
    # трейсбеком і зламає скасування корутини.
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:  # noqa: BLE001 — тільки рахуємо, обробка вище за стеком
        raise
    finally:
        # Власні скрейпи Prometheus не рахуємо. Він ходить на /metrics раз на
        # 60 с до КОЖНОЇ репліки — це тисячі «запитів» на добу, яких не робив
        # жоден користувач. Інакше sum(rate(http_requests_total[5m])) показує
        # трафік моніторингу і видає його за навантаження сервісу.
        if path != "/metrics":
            HTTP_REQUESTS.labels(
                method=request.method, path=path, status=str(status)
            ).inc()
            # Гістограму наповнюємо лише для /predict — і включно з 422, бо биті
            # запити теж займають час сервісу.
            if path == "/predict":
                PREDICT_LATENCY.observe(time.perf_counter() - start)

    return response


class IrisFeatures(BaseModel):
    # Порядок полів = порядок ознак у load_iris. Pydantic сам відбиває
    # сміття на кшталт {"sepal_width": "abc"} кодом 422 ще до моделі —
    # це і є валідація на межі довіри, і саме вона наповнює панель помилок.
    sepal_length: float
    sepal_width: float
    petal_length: float
    petal_width: float


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    # Дефолтний обробник FastAPI мовчить у логах — а 422 у демо навмисні
    # (генератор шле 5% битих payload). Логуємо їх у тому ж JSON-форматі,
    # щоб у Loki вони знаходилися одним запитом.
    log(
        level="WARNING",
        event="validation_error",
        request_id=uuid.uuid4().hex,
        # input обовʼязковий: без нього в Loki видно ЩО зламалось, але не
        # ВІД ЧОГО. Демонстрація «знайшли биті запити і подивились їхні дані»
        # без цього поля обривається на півдорозі.
        input=exc.body,
        errors=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()],
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.post("/predict")
def predict(features: IrisFeatures):
    # Синхронний def — FastAPI виконає його у threadpool. Це навмисно:
    # predict_proba блокує CPU на кілька мілісекунд, і в async-функції
    # він би блокував увесь event loop разом зі скрейпом /metrics.
    request_id = uuid.uuid4().hex
    payload = features.model_dump()

    # Посилання на STATE беремо РІВНО ОДИН раз: reload_model підмінює словник
    # цілком, тож усі три поля нижче гарантовано з однієї моделі. Читали б
    # STATE["..."] тричі — reload посеред запиту дав би класи однієї версії
    # з ймовірностями іншої.
    state = STATE
    start = time.perf_counter()
    proba = state["model"].predict_proba([list(payload.values())])[0]
    index = int(proba.argmax())
    predicted_class = state["classes"][index]
    confidence = float(proba[index])  # float() — numpy-типи не серіалізуються в JSON
    inference = time.perf_counter() - start

    # PREDICT_LATENCY тут НЕ чіпаємо — її наповнює middleware повним часом
    # відповіді. Тут лише час самого інференсу, і в лог він іде під іншим
    # іменем (inference_ms), щоб цифри в логах і на графіку не суперечили
    # одна одній: це різні величини, а не розбіжність.
    PREDICT_REQUESTS.labels(predicted_class=predicted_class).inc()
    PREDICT_CONFIDENCE.labels(predicted_class=predicted_class).observe(confidence)

    # request_id живе В ТІЛІ логу, а НЕ в лейблі Loki: Loki будує окремий
    # стрім на кожну унікальну комбінацію лейблів, тож унікальний ID на
    # запит = мільйони стрімів = Loki лягає. Лейбли — лише низькокардинальні
    # (namespace, app, pod), решта фільтрується на льоту через `| json`.
    log(
        level="INFO",
        event="predict",
        request_id=request_id,
        input=payload,
        prediction=predicted_class,
        confidence=round(confidence, 4),
        inference_ms=round(inference * 1000, 2),
        # ⭐ ТЕМА 11: яка саме версія моделі дала цю відповідь.
        # Доти в логу цього не було, і наслідок був неочевидний: дріфт-експортер
        # читає з Loki рівно ці рядки, тож ФІЗИЧНО не міг відрізнити прогнози
        # champion від challenger. Після blue-green у стрімі опиняються обидві
        # моделі, і без цього поля хі-квадрат по класах змішував би їх у кашу.
        # У тілі логу, а не в лейблі Loki: версій з часом стають десятки, а
        # лейбл — це окремий стрім на кожне значення.
        model_version=state["version"],
    )

    return {
        "request_id": request_id,
        "prediction": predicted_class,
        "confidence": round(confidence, 4),
        "probabilities": {c: round(float(p), 4) for c, p in zip(state["classes"], proba)},
        "model_version": state["version"],
    }


@app.get("/healthz")
def healthz():
    # Навмисно без звернення до моделі: якщо pickle не завантажився, процес
    # взагалі не стартує (виняток на імпорті), тож окремо перевіряти нічого.
    # Той самий ендпоїнт годиться і для liveness, і для readiness.
    state = STATE  # одне читання посилання — див. коментар у predict()
    return {"status": "ok", "model": state["model_type"],
            "version": state["version"], "source": state["source"]}


@app.get("/metrics")
def metrics():
    # Віддаємо руками, а не через prometheus_client.make_asgi_app(): змонтований
    # sub-app не проходить через наш middleware і не мав би route у /docs.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _model_label():
    """Що саме зараз обслуговує: версія з реєстру чи зашита в образ."""
    if STATE["source"] == "registry":
        return f'{MODEL_NAME} v{STATE["version"]} ({MODEL_ALIAS})'
    return "зашита в образ"


def _source_label():
    if STATE["source"] == "registry":
        return f'реєстр MLflow — promote змінює її без перезбірки'
    return "образ (MLflow недоступний або вимкнений)"


def _metrics_label():
    m = STATE.get("meta") or {}
    bits = []
    if m.get("accuracy") is not None:
        bits.append(f'accuracy {m["accuracy"]:.3f}')
    if m.get("f1") is not None:
        bits.append(f'f1 {m["f1"]:.3f}')
    p = m.get("params") or {}
    if p.get("n_estimators"):
        bits.append(f'n_estimators={p["n_estimators"]}, max_depth={p.get("max_depth")}')
    if m.get("run_id"):
        bits.append(f'run {m["run_id"]}')
    return (", " + ", ".join(bits)) if bits else ""


def _lineage_rows():
    """Рядки таблиці «звідки взялась ця модель» — теги версії з реєстру.

    ТЕМА 11, СЛАЙД 16. Доти сторінка показувала лише метрики, тобто відповідала
    на питання «наскільки вона добра», але не на «звідки вона взялась». Теги
    ставить train.py при реєстрації; якщо їх немає (модель зашита в образ або
    натренована до Теми 11) — блок просто не малюється.
    """
    tags = (STATE.get("meta") or {}).get("tags") or {}
    if not tags:
        return ""

    human = {
        "dataset": "дані",
        "dataset_digest": "хеш даних",
        "git_sha": "коміт",
        "trained_by": "хто тренував",
        "status": "статус",
    }
    rows = []
    for key, label in human.items():
        value = tags.get(key)
        if not value or value == "unknown":
            continue
        # Коміт скорочуємо: 40 символів у таблиці нечитабельні, а 12 однозначні.
        if key == "git_sha":
            value = value[:12]
        rows.append(f"<tr><td class='k'>{label}</td><td><code>{value}</code></td></tr>")

    aliases = (STATE.get("meta") or {}).get("aliases") or []
    if aliases:
        rows.append("<tr><td class='k'>аліаси</td><td>"
                    + " ".join(f"<code>@{a}</code>" for a in aliases) + "</td></tr>")
    if not rows:
        return ""
    return ("<h2>Звідки взялась ця модель</h2><table>" + "".join(rows) + "</table>")


@app.get("/", response_class=HTMLResponse)

def index():
    # Щоб студент, зробивши port-forward, побачив список ендпоїнтів,
    # а не голий 404 від FastAPI.
    return f"""<!doctype html>
<html lang="uk"><meta charset="utf-8"><title>ML-сервіс Iris</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:44rem;margin:3rem auto;padding:0 1rem}}
code{{background:#f4f4f5;padding:.1rem .3rem;border-radius:.2rem}}
.badge{{font-size:1.05rem}}
.badge b{{background:#e8f0fe;color:#1a4b8c;padding:.15rem .5rem;border-radius:.3rem}}
.dim{{color:#8a8a94;font-size:.85rem}}
h2{{font-size:1.05rem;margin:1.6rem 0 .4rem}}
table{{border-collapse:collapse;font-size:.9rem}}
td{{padding:.15rem .8rem .15rem 0;vertical-align:top}}
td.k{{color:#8a8a94;white-space:nowrap}}</style>
<h1>ML-сервіс Iris</h1>
<p class="badge">модель <b>{_model_label()}</b> · джерело: {_source_label()}</p>
<p>{STATE["model_type"]}, sklearn {BUNDLE["sklearn_version"]}{_metrics_label()}</p>
<p class="dim">образ сервісу: {APP_VERSION}</p>
{_lineage_rows()}
<ul>
  <li><code>POST /predict</code> — передбачення, приклад нижче</li>
  <li><a href="/healthz"><code>GET /healthz</code></a> — перевірка живості</li>
  <li><a href="/metrics"><code>GET /metrics</code></a> — метрики Prometheus</li>
  <li><a href="/docs"><code>GET /docs</code></a> — інтерактивна Swagger-документація</li>
</ul>
<pre><code>curl -X POST http://localhost:8000/predict \\
  -H 'Content-Type: application/json' \\
  -d '{{"sepal_length":5.1,"sepal_width":3.5,"petal_length":1.4,"petal_width":0.2}}'</code></pre>
</html>"""
