"""Експортер дріфту даних: Loki -> KS-тест / хі-квадрат -> Prometheus.

ЧОМУ scipy, а не Evidently / Alibi Detect (слайди 35-36, 41-43):
  • scipy вже є в образі Теми 8 (scipy==1.18.0), а KS-тест — це ОДИН виклик.
  • Evidently 0.7.21 не має експортера в Prometheus: модуль
    evidently.model_monitoring, на якому побудовані всі туторіали, вилучено.
    Тобто цей файл довелося б написати однаково, але образ виріс би на
    ~500-700 MiB (pyarrow + plotly + litestar). Залежність беруть тоді, коли
    вона ВИДАЛЯЄ твій код; ця його не видаляє.
  • Alibi Detect 0.13 прибитий до numpy<2.0.0, а модель Теми 8 і mlflow
    3.15.1 стоять на numpy 2.5.2. Один образ їх не вміщує фізично.
Стрілку «аналіз -> Prometheus» зі слайда 35 пише інженер. Ось вона.

Джерело «поточних» даних — Loki: модель Теми 8 уже логує кожен запит рядком
JSON з полем input, а Alloy уже складає ці рядки в Loki. Нових залежностей у
модель не додаємо, спільний том не потрібен (PVC у цьому кластері зламані).

Ендпоїнти (порт 9100): /metrics, /healthz, /simulate-drift?shift=0.8
"""

import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from functools import lru_cache
from wsgiref.simple_server import WSGIRequestHandler, make_server

import numpy as np
from prometheus_client import REGISTRY, Gauge, make_wsgi_app
from scipy.stats import chisquare, ks_2samp
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

# ─────────────────────────────────────────────────────────────
# Налаштування
# ─────────────────────────────────────────────────────────────
LOKI_URL = os.getenv("LOKI_URL", "http://loki.logging.svc.cluster.local:3100")
# ⭐ Тема 11: еталон береться з ТОГО САМОГО файла, на якому вчилась модель.
# Порожньо = вбудований load_iris() (так було до Теми 11).
DATASET_URI = os.getenv("DATASET_URI", "s3://datasets/iris/v2.csv")

LOKI_QUERY = os.getenv("LOKI_QUERY", '{app="ml-model"} |= "predict"')
WINDOW_MINUTES = int(os.getenv("WINDOW_MINUTES", "10"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
# Порог p-value. 0.01, а НЕ підручникові 0.05 — і це ЗАМІРЯНО, не на смак.
# 200 нормальних вікон по 3000 записів:
#   KS по 4 ознаках      — 0/200 хибних тривог при обох порогах;
#   хі-квадрат прогнозів — 12/200 (6%) при 0.05 і лише 4/200 (2%) при 0.01.
# При alpha=0.05 тест ПО ВИЗНАЧЕННЮ бреше у 5% випадків: за годину заняття
# (перевірка раз на хвилину) це ~3 червоні панелі на чистих даних, і студент
# перестає вірити дашборду. Реальний дріфт дає p ~ 1e-11, тож нижчий поріг
# виявлення не псує — запас лишається дев'ять порядків.
P_THRESHOLD = float(os.getenv("P_THRESHOLD", "0.01"))
# KS на 5 точках — це не тест, а генератор випадкових чисел. Нижче цієї межі
# нічого не рахуємо і НЕ скидаємо drift_detected у 0 (інакше при паузі в
# трафіку дашборд «зеленіє» і бреше, що дріфт зник).
MIN_SAMPLES = int(os.getenv("MIN_SAMPLES", "30"))
PORT = int(os.getenv("PORT", "9100"))

FEATURES = ("sepal_length", "sepal_width", "petal_length", "petal_width")

# Стан симуляції (ендпоїнт /simulate-drift). shift=0 => читаємо справжній Loki.
# SIM_JITTER=0.05 теж заміряно: при 0.10 сам джитер перекидає прикордонні
# зразки через межі класів, і хі-квадрат бачить «дріфт» на чистих даних
# у 6% вікон. Лишаємо ручкою в env — калібрування під реальні дані.
SIM = {
    "shift": float(os.getenv("SIMULATE_SHIFT", "0")),
    "samples": int(os.getenv("SIM_SAMPLES", "400")),
    "jitter": float(os.getenv("SIM_JITTER", "0.05")),
}

# ─────────────────────────────────────────────────────────────
# Метрики — імена рівно з контракту, дашборд Grafana читає саме їх
# ─────────────────────────────────────────────────────────────
DRIFT_DETECTED = Gauge("drift_detected", "Дріфт виявлено (1) чи ні (0)", ["feature"])
DRIFT_P_VALUE = Gauge("drift_p_value", "p-value статистичного тесту", ["feature"])
DRIFT_TIMESTAMP = Gauge("drift_check_timestamp_seconds", "Unix-час останньої перевірки")
PREDICTION_SHARE = Gauge("prediction_class_share", "Частка класу у передбаченнях", ["class"])
REFERENCE_SIZE = Gauge("reference_dataset_size", "Розмір еталонного набору")
CURRENT_SIZE = Gauge("current_window_size", "Розмір поточного вікна")
# Звідки взявся еталон. Потрібна саме метрика, а не лише лог: якщо експортер
# тихо впав на вбудований sklearn, усі p-value стають недостовірними, і це
# має бути видно на дашборді, а не лише тому, хто читав логи при старті.
REFERENCE_SOURCE = Gauge("reference_source", "Джерело еталона (1)", ["source", "uri"])


# ─────────────────────────────────────────────────────────────
# Еталон: той самий train-split, на якому вчилася модель Теми 8
# ─────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _train_split():
    """Ті самі аргументи, що в train.py Теми 8 і в train_mlflow.py
    (test_size=0.2, random_state=42, stratify) => БАЙТ-У-БАЙТ ті 120 рядків,
    на яких модель тренувалась. lru_cache — щоб не перечитувати датасет
    на кожній перевірці."""
    data = load_iris()
    X_train, _, y_train, _ = train_test_split(
        data.data, data.target, test_size=0.2, random_state=42, stratify=data.target
    )
    # str() навмисно: target_names — це numpy.str_, а не звичайний рядок. Він
    # працює як str (той самий hash), тому помилка була б ТИХОЮ: лейбл у
    # Prometheus виглядав би правильно, а порівняння з рядками з JSON
    # трималося б на деталі реалізації numpy.
    return X_train, y_train, [str(n) for n in data.target_names]


def _reference_from_storage(uri: str):
    """Еталон із того самого CSV, на якому вчилася модель.

    🔴 НАВІЩО ЦЕ ЗʼЯВИЛОСЬ У ТЕМІ 11. Доти еталон брався з `load_iris()` — і це
    було правильно, бо тренування брало звідти ж. Щойно тренування переїхало на
    s3://datasets/iris/v2.csv, зашитий еталон на 150 рядків почав описувати
    ІНШИЙ розподіл. KS-тест показував би дріфт на спокійному трафіку — тобто
    експортер брехав би, і найгіршим способом: правдоподібно.

    Читання зі сховища — спільний модуль datasets_common: там одне визначення
    відбитка і одна документована пастка з endpoint_url.
    """
    from datasets_common import read_csv  # локальний імпорт: без сховища не потрібен

    df, digest = read_csv(uri)
    rows = df.to_dict("records")
    # Той самий split, що й у train.py: еталон — це TRAIN-частина, а не весь
    # файл. Інакше в еталон потрапили б рядки, яких модель не бачила.
    names = sorted({r["target"] for r in rows})
    y = [names.index(r["target"]) for r in rows]
    idx_train, _, y_train, _ = train_test_split(
        list(range(len(rows))), y, test_size=0.2, random_state=42, stratify=y
    )
    ref = {f: np.array([float(rows[i][f]) for i in idx_train]) for f in FEATURES}
    return ref, y_train, names


@lru_cache(maxsize=1)
def load_reference():
    """Еталон: спершу зі сховища, у крайньому разі — з пакета sklearn.

    Фолбек лишається навмисно: под мусить піднятись, навіть коли MinIO лежить.
    Але він ГУЧНИЙ — подія в лозі й метрика `reference_source`, — бо мовчазний
    відкат на інші дані означав би, що дріфт рахується не проти того, на чому
    вчилась модель.
    """
    source = "builtin"
    if DATASET_URI:
        try:
            ref, y_train, names = _reference_from_storage(DATASET_URI)
            source = "storage"
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"event": "reference_fallback", "uri": DATASET_URI,
                              "error": str(e), "hint": "еталон із sklearn; запустіть make seed"},
                             ensure_ascii=False), flush=True)
            X_train, y_train, names = _train_split()
            ref = {f: X_train[:, i] for i, f in enumerate(FEATURES)}
    else:
        X_train, y_train, names = _train_split()
        ref = {f: X_train[:, i] for i, f in enumerate(FEATURES)}

    # Еталонний розподіл класів рахуємо З ДАНИХ, а не вписуємо 0.333 руками:
    # вписана константа розійдеться з реальністю, щойно хтось змінить test_size.
    counts = Counter(names[i] for i in y_train)
    total = sum(counts.values())
    ref_shares = {c: counts[c] / total for c in counts}
    REFERENCE_SIZE.set(total)
    REFERENCE_SOURCE.labels(source=source, uri=DATASET_URI or "sklearn").set(1)
    print(json.dumps({"event": "reference_loaded", "source": source,
                      "uri": DATASET_URI or "sklearn:load_iris", "rows": total},
                     ensure_ascii=False), flush=True)
    return ref, ref_shares


# ─────────────────────────────────────────────────────────────
# Поточне вікно: читаємо власні логи моделі з Loki
# ─────────────────────────────────────────────────────────────
def fetch_current(window_minutes=WINDOW_MINUTES):
    """Тягне лог-рядки з Loki за останні N хвилин і збирає з них ознаки.

    Loki тут — просте сховище рядків: JSON розбираємо в Python, а не через
    `| json` у LogQL. Так ми не залежимо від того, як Loki розплющує
    вкладений об'єкт input у пласкі поля.
    """
    end = time.time_ns()  # Loki чекає НАНОсекунди, не секунди
    start = end - window_minutes * 60 * 1_000_000_000
    params = urllib.parse.urlencode(
        {
            "query": LOKI_QUERY,
            "start": str(start),
            "end": str(end),
            # 5000 — дефолтний max_entries_limit_per_query у Loki; більше
            # просити не можна, Loki відповість 400. direction=backward, тож
            # при переповненні візьмемо НАЙСВІЖІШІ 5000, а не найдавніші.
            # 10 хв * 5 RPS = 3000 записів, тобто запас ~1.7x.
            "limit": "5000",
            "direction": "backward",
        }
    )
    url = f"{LOKI_URL}/loki/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        payload = json.load(resp)
    return parse_streams(payload)


def parse_streams(payload):
    """Витягує ознаки й передбачення з відповіді Loki. Окремою функцією —
    щоб її можна було перевірити без кластера (див. test_drift.py)."""
    current = {f: [] for f in FEATURES}
    predictions = []
    for stream in payload.get("data", {}).get("result", []):
        for _ts, line in stream.get("values", []):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # у стрімі є й не-JSON рядки (uvicorn на старті)
            if rec.get("event") != "predict":
                continue
            features = rec.get("input") or {}
            # Беремо рядок лише якщо всі 4 ознаки на місці й числові:
            # напіврядок зіпсував би KS-тест ТИХО, без помилки.
            try:
                values = [float(features[f]) for f in FEATURES]
            except (KeyError, TypeError, ValueError):
                continue
            for f, v in zip(FEATURES, values):
                current[f].append(v)
            if rec.get("prediction"):
                predictions.append(rec["prediction"])
    return current, predictions


# ─────────────────────────────────────────────────────────────
# Симуляція дріфту для заняття (ендпоїнт /simulate-drift)
# ─────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _local_model():
    """БАЙТ-У-БАЙТ та сама модель, що в Темі 8: ті самі дані, ті самі
    n_estimators=100 і random_state=42 (model/train.py). Тренування на 120
    рядках — ~50 мс, один раз за життя пода, тому окремий образ чи
    завантаження pickle тут не потрібні.

    Потрібна ЛИШЕ для симуляції: у нормальному режимі класи беруться з логів
    справжньої моделі, і ця функція не викликається взагалі.
    """
    X, y, _names = _train_split()
    return RandomForestClassifier(n_estimators=100, random_state=42).fit(X, y)


def simulate_window(shift, samples=None, seed=None):
    """Синтетичне «поточне вікно»: реальні рядки еталона + зсув на shift.

    ОСНОВНА ручка дріфту на занятті — env DRIFT_SHIFT у генераторі трафіку
    Теми 8 (дріфт за визначенням приходить ЗЗОВНІ, а не з самого сервісу).
    Ця симуляція — швидкий дублер: показує падіння p-value НЕГАЙНО і працює,
    навіть якщо Loki ще не наповнився або взагалі не піднятий.

    Семплить ТІ САМІ 120 рядків, що є еталоном, з малим джитером — тож при
    shift=0 дріфту немає ЗА ПОБУДОВОЮ, а не завдяки везінню.
    """
    X, _y, names = _train_split()
    rnd = random.Random(seed)
    rows = X.tolist()
    batch = []
    for _ in range(samples or SIM["samples"]):
        row = rnd.choice(rows)
        # max(0.1, ...) — довжина пелюстки не може бути відʼємною; той самий
        # запобіжник, що й у генераторі трафіку Теми 8.
        batch.append([max(0.1, rnd.gauss(v + shift, SIM["jitter"])) for v in row])

    current = {f: [row[i] for row in batch] for i, f in enumerate(FEATURES)}
    # Класи проставляє локальна копія моделі — щоб у симуляції був видимий
    # ще й PREDICTION drift (частки класів на слайді 34), а не лише зсув ознак.
    predictions = [names[int(k)] for k in _local_model().predict(batch)]
    return current, predictions


def collect_window():
    """Джерело поточного вікна: Loki або симуляція (див. /simulate-drift)."""
    if SIM["shift"]:
        return simulate_window(SIM["shift"])
    return fetch_current()


# ─────────────────────────────────────────────────────────────
# Власне перевірка дріфту — уся «магія» це два виклики scipy
# ─────────────────────────────────────────────────────────────
def check_drift(reference, ref_shares, current, predictions):
    """Оновлює метрики. Повертає True, якщо тест реально виконувався.

    KS-тест (Колмогорова-Смирнова) для ЧИСЛОВИХ ознак: рахує максимальну
    відстань між емпіричною функцією розподілу еталона й поточних даних.
    H0 (нульова гіпотеза) — «розподіли однакові». p-value — ймовірність
    побачити таку відстань ВИПАДКОВО, якщо H0 правдива. Малий p => такий
    збіг занадто неймовірний => H0 відкидаємо => це дріфт.
    Важливо розуміти: під H0 p-value САМ по собі випадкова величина,
    рівномірна на [0,1]. Тому на чистих даних він «гуляє» 0.1-0.9, і це
    норма, а не нестабільність експортера.

    Хі-квадрат для передбачень: клас — категоріальна величина, KS до неї не
    застосовний. Порівнюємо спостережені частоти класів з очікуваними
    (частка_еталона * розмір_вікна). Це і є PREDICTION drift зі слайда 34.
    """
    n = len(current[FEATURES[0]])
    CURRENT_SIZE.set(n)
    if n < MIN_SAMPLES:
        # Свідомо НЕ чіпаємо drift_detected: краще показати старе значення,
        # ніж збрехати нулем на порожньому вікні. Дашборд, який зеленіє від
        # відсутності даних, — найнебезпечніший вид дашборда.
        return False

    for f in FEATURES:
        result = ks_2samp(reference[f], current[f])
        DRIFT_P_VALUE.labels(feature=f).set(result.pvalue)
        DRIFT_DETECTED.labels(feature=f).set(int(result.pvalue < P_THRESHOLD))

    if predictions:
        counts = Counter(predictions)
        classes = sorted(ref_shares)
        observed = [counts.get(c, 0) for c in classes]
        # Знаменник — сума ВІДОМИХ класів, а НЕ len(predictions). Один рядок у
        # Loki з чужим лейблом (стара модель, інший датасет, ручний curl) дав
        # би sum(observed) < sum(expected), а scipy 1.18 на розбіжність сум
        # понад 1.5e-08 кидає ValueError "the sum of the observed frequencies
        # must agree with the sum of the expected frequencies" — і под іде в
        # CrashLoopBackOff. Так суми збігаються ЗА ПОБУДОВОЮ: невідомий клас
        # просто не потрапляє ні в тест, ні в частки.
        total = sum(observed)
        if total:
            for c, o in zip(classes, observed):
                PREDICTION_SHARE.labels(**{"class": c}).set(o / total)
            # Клас, якого модель не передбачила ні разу, дає expected>0,
            # observed=0 — це коректно для хі-квадрата й саме так виглядає
            # prediction drift.
            expected = [ref_shares[c] * total for c in classes]
            # Правило Кокрена: хі-квадрат достовірний, лише поки ОЧІКУВАНА
            # частота в КОЖНІЙ комірці >= 5. При MIN_SAMPLES=30 і найменшій
            # частці еталона 1/3 виходить 10 — проходить; на вікні з 12
            # записів було б 4, і p-value був би не статистикою, а шумом.
            # Тоді метрику НЕ оновлюємо взагалі (та сама логіка, що й при
            # n < MIN_SAMPLES): застаріле значення чесніше за свіже брехливе.
            # Ця ж умова прибирає expected=0, тобто ділення на нуль у тесті.
            if min(expected) >= 5:
                p = float(chisquare(observed, expected).pvalue)
                DRIFT_P_VALUE.labels(feature="prediction").set(p)
                DRIFT_DETECTED.labels(feature="prediction").set(int(p < P_THRESHOLD))

    DRIFT_TIMESTAMP.set(time.time())
    return True


# HTTP-потік (/simulate-drift) і фоновий цикл main() викликають run_check
# НЕЗАЛЕЖНО один від одного. Без цього замка два вікна пишуть у ті самі Gauge
# впереміш, а відповідь /simulate-drift збирається з p-value ДВОХ різних
# вікон — вона читає значення назад із REGISTRY. Одна перевірка ~50 мс, тож
# черга з двох нікому не заважає.
_CHECK_LOCK = threading.Lock()


def run_check():
    """Один цикл: узяти вікно -> порахувати -> віддати короткий підсумок."""
    with _CHECK_LOCK:
        return _run_check()


def _run_check():
    reference, ref_shares = load_reference()
    current, predictions = collect_window()
    evaluated = check_drift(reference, ref_shares, current, predictions)
    return {
        "event": "drift_check",
        "source": "simulation" if SIM["shift"] else "loki",
        "window": len(current[FEATURES[0]]),
        "evaluated": evaluated,
        # Читаємо значення НАЗАД із реєстру Prometheus, а не з локальних
        # змінних: так у логах і у відповіді /simulate-drift рівно те, що
        # побачить Prometheus. Розбіжність між логом і метрикою неможлива.
        "p_value": {
            f: REGISTRY.get_sample_value("drift_p_value", {"feature": f})
            for f in FEATURES + ("prediction",)
        },
        "drift_detected": {
            f: REGISTRY.get_sample_value("drift_detected", {"feature": f})
            for f in FEATURES + ("prediction",)
        },
    }


# ─────────────────────────────────────────────────────────────
# HTTP: /metrics, /healthz, /simulate-drift
# ─────────────────────────────────────────────────────────────
# wsgiref зі stdlib замість prometheus_client.start_http_server(): нам
# потрібні ще /healthz і /simulate-drift, а start_http_server віддає метрики
# на будь-якому шляху і маршрутизації не має. Свій FastAPI/uvicorn тут був би
# третьою залежністю заради трьох if-ів.
_metrics_app = make_wsgi_app()


def _json_response(start_response, status, body):
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    start_response(
        status,
        [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(raw)))],
    )
    return [raw]


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")

    if path == "/healthz":
        # Навмисно НЕ ходить у Loki: liveness-проба не мусить падати через
        # чужий сервіс, інакше рестарт Loki рестартує ще й експортер, і ми
        # втрачаємо метрики саме тоді, коли вони найпотрібніші.
        last = REGISTRY.get_sample_value("drift_check_timestamp_seconds")
        return _json_response(start_response, "200 OK", {
            "status": "ok",
            "simulated_shift": SIM["shift"],
            # None, а не «півтора мільярда секунд»: до першої успішної
            # перевірки Gauge дорівнює 0, і різниця з time() була б сміттям.
            "last_check_age_seconds": round(time.time() - last, 1) if last else None,
        })

    if path == "/simulate-drift":
        # GET, а не POST — щоб на занятті працювало і з браузера:
        #   curl 'http://localhost:9100/simulate-drift?shift=0.8'   увімкнути
        #   curl 'http://localhost:9100/simulate-drift?shift=0'     вимкнути
        raw = urllib.parse.parse_qs(environ.get("QUERY_STRING", "")).get("shift", ["0.8"])[0]
        try:
            SIM["shift"] = float(raw)
        except ValueError:
            return _json_response(start_response, "400 Bad Request",
                                  {"error": "shift мусить бути числом", "got": raw})
        # Перераховуємо ВІДРАЗУ, щоб не чекати CHECK_INTERVAL: студент бачить
        # нові p-value у тій самій відповіді, а не через хвилину.
        try:
            result = run_check()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            # Типовий випадок: shift=0 (вимкнули симуляцію), а Loki лежить.
            # Значення shift УЖЕ застосовано, тому повідомляємо це прямо,
            # а не віддаємо трейсбек, який виглядає як зламаний експортер.
            return _json_response(start_response, "503 Service Unavailable", {
                "event": "loki_error", "simulated_shift": SIM["shift"], "error": str(e)})
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return _json_response(start_response, "200 OK", result)

    # Усе інше — метрики Prometheus (це і /metrics, і будь-який інший шлях).
    return _metrics_app(environ, start_response)


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        # Тихо: інакше КОЖЕН скрейп Prometheus (раз на 60 с) додає у логи
        # рядок у форматі Apache і забиває JSON-логи, які ми віддаємо в Loki.
        pass


def serve(port=PORT):
    # ponytail: однопотоковий wsgiref — скрейпи серіалізуються. Для одного
    # Prometheus і одного /simulate-drift цього досить; якщо колись
    # знадобиться паралельність — prometheus_client.exposition має
    # ThreadingWSGIServer, підмінити клас сервера.
    # port=0 => ядро дає вільний порт (так робить test_drift.py всередині
    # пода, де 9100 уже зайнятий самим експортером); реальний номер потім
    # читається як server.server_port.
    server = make_server("", port, application, handler_class=_QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    load_reference()  # прогріваємо еталон ДО першого скрейпу
    serve()
    print(json.dumps({"event": "exporter_started", "port": PORT,
                      "endpoints": ["/metrics", "/healthz", "/simulate-drift?shift=0.8"]}),
          flush=True)

    while True:
        try:
            print(json.dumps(run_check(), ensure_ascii=False), flush=True)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            # Loki може бути недоступним (рестарт, emptyDir) — це не причина
            # падати: под у CrashLoopBackOff не віддає навіть старих метрик,
            # і дашборд стає порожнім замість «застарілого».
            print(json.dumps({"event": "loki_error", "error": str(e)}), flush=True)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
