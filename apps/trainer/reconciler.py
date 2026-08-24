"""Узгоджувач даних: тренування запускається, коли дані РОЗІЙШЛИСЬ із моделлю.

ЧОМУ НЕ ПРОСТО РЕАКЦІЯ НА ПОДІЮ
────────────────────────────────
Перша версія цього компонента слухала webhook MinIO і на кожну подію смикала
GitHub. Зміряно на живому прогоні, чим це закінчується:

  • `make seed` залив ТРИ файли → три події → три одночасні тренування;
  • один із трьох упав: пік на CoreDNS, под не зарезолвив mlflow;
  • перезалив ІДЕНТИЧНОГО вмісту теж запускав тренування — дані ті самі,
    робота марна;
  • а якби под лежав у момент заливки, подія загубилась би НАЗАВЖДИ, і ніхто
    б не дізнався.

Це не вади реалізації, а властивості edge-triggered підходу: реакція на факт
зміни. Лікування — level-triggered: дивитись не на подію, а на СТАН.

  ПИТАННЯ НЕ «чи щось сталось», А «чи розійшлись дані з моделлю».

Стан порівнюється так:

    чи є в реєстрі версія з тегом dataset_digest = відбиток файла?
            немає →  тренуємо
            є     →  нічого не робимо (уже пробували, результат відомий)

Звідси безкоштовно випливає все, чого бракувало:
  • дедуплікація — три файли дають одне порівняння;
  • ідемпотентність — перезалив того самого вмісту нічого не запускає;
  • події не губляться — навіть якщо под лежав, наступний цикл усе побачить;
  • захист від паралельних прогонів — перевіряємо, чи вже щось біжить.

Рівно так працює ArgoCD: опитує Git раз на 3 хвилини, а webhook лише
ПРИСКОРЮЄ цикл, не замінюючи його. Тут так само: HTTP-ендпоїнт будить цикл,
але не є єдиним шляхом.

Ендпоїнти (порт 8080):
    POST /            подія MinIO — будить цикл (прискорювач)
    GET  /healthz     проба
    GET  /metrics     метрики Prometheus
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import mlflow
from datasets_common import fetch
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

DATASET_URI = os.getenv("DATASET_URI", "s3://datasets/iris/v2.csv")
MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", "iris-rf")
MODEL_ALIAS = os.getenv("MODEL_ALIAS", "champion")
# Період узгодження. 300 c — той самий порядок, що дефолтний resync ArgoCD
# (180 c): достатньо часто, щоб не проґавити, достатньо рідко, щоб не шуміти.
RESYNC = int(os.getenv("RESYNC_SECONDS", "300"))
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
GH_REPO = os.getenv("GITHUB_REPO", "alexnodejs/mds06-mlops-platform")
EVENT_TYPE = os.getenv("EVENT_TYPE", "dataset-changed")
# Скільки версій датасету відповідають одному аліасу моделі. Ми стежимо рівно
# за тим файлом, на якому вчиться прод; v1 і v3 — навчальні, їх запускають руками.
DATASET_NAME = DATASET_URI.rsplit("/", 1)[-1].removesuffix(".csv")

RECONCILES = Counter("dataset_reconcile_total", "Цикли узгодження", ["result"])
TRIGGERS = Counter("dataset_trigger_total", "Запуски тренування", ["result"])
DRIFTED = Gauge("dataset_out_of_sync", "1 = дані розійшлись із чинною моделлю")
LAST_RUN = Gauge("dataset_reconcile_timestamp_seconds", "Unix-час останнього циклу")

_wake = threading.Event()


def log(**f):
    print(json.dumps(f, ensure_ascii=False, default=str), flush=True)


def storage_digest():
    """Відбиток файла в сховищі. Визначення — одне на весь репозиторій
    (datasets_common.sha12): sha256 байтів, перші 12 символів.

    🔴 Тут була пастка, яка мало не дала нескінченний цикл: тег dataset_digest
    моделі раніше містив ВНУТРІШНІЙ хеш MLflow, а не sha256 файла. Два різні
    алгоритми в порівнянні означають «завжди різні» — узгоджувач тренував би
    на кожному циклі й виглядав би при цьому цілком робочим.
    """
    body, digest = fetch(DATASET_URI)
    return digest, len(body)


def already_trained(digest):
    """Чи тренували ми вже НА ЦИХ даних — байдуже, з яким результатом.

    🔴 ТУТ БУВ НЕСКІНЧЕННИЙ ЦИКЛ, і його варто розібрати на занятті.
    Перша версія порівнювала відбиток сховища з тегом ЧИННОГО @champion. Логіка
    здавалась очевидною: «модель у проді навчена не на тих даних — тренуємо».
    Але quality gate має повне право ВІДХИЛИТИ нову модель: дані змінились, а
    краще не стало. Тоді champion лишається старим, розбіжність нікуди не
    дінеться — і наступний цикл запускає те саме тренування. І наступний. І так
    кожні пʼять хвилин, вічно, з виглядом цілком робочої системи.

    Правильне питання не «чи навчений прод на цих даних», а «чи ми вже
    ПРОБУВАЛИ ці дані». Якщо будь-яка версія в реєстрі має цей відбиток —
    пробували, і результат уже відомий. Повторювати немає сенсу.

    Це та сама відмінність, що між «стан не збігається з бажаним» і «ми вже
    зробили все, що могли»: узгоджувач мусить зупинятись на другому.
    """
    try:
        found = mlflow.MlflowClient().search_model_versions(
            f"name = '{MODEL_NAME}' and tags.dataset_digest = '{digest}'", max_results=1)
        return len(found) > 0
    except Exception as e:  # noqa: BLE001
        # Не змогли перевірити — вважаємо, що тренували. Зайвий пропуск
        # виправить наступний цикл; зайве тренування коштує грошей і часу.
        log(event="registry_check_failed", error=str(e), decision="пропускаю цикл")
        return True


def champion_digest():
    """Відбиток даних чинної моделі — лише для логів і метрики.

    Рішення на ньому НЕ будується (див. already_trained), але бачити його
    корисно: він відповідає на питання «на чому працює прод просто зараз».
    """
    try:
        mv = mlflow.MlflowClient().get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
        return (mv.tags or {}).get("dataset_digest")
    except Exception:  # noqa: BLE001 — немає моделі чи аліаса: це не помилка
        return None


def _gh(path, method="GET", body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GH_REPO}{path}",
        data=body, method=method,
        headers={"Authorization": f"Bearer {GH_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "mds06-dataset-reconciler"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, (r.read() if method == "GET" else b"")


def training_running():
    """Чи вже біжить прогін. Захист від паралельних тренувань однієї моделі.

    Питаємо GitHub, а не Step Functions: токен у нас уже є, а креденшелів AWS
    у кластері немає — і саме щоб їх не заводити, увесь ланцюг іде через GitHub.
    """
    if not GH_TOKEN:
        return False
    try:
        _, raw = _gh("/actions/workflows/train.yml/runs?status=in_progress&per_page=1")
        if json.loads(raw).get("total_count", 0):
            return True
        _, raw = _gh("/actions/workflows/train.yml/runs?status=queued&per_page=1")
        return bool(json.loads(raw).get("total_count", 0))
    except Exception as e:  # noqa: BLE001
        # Не змогли перевірити — вважаємо, що біжить. Пропустити цикл дешевше,
        # ніж запустити п'яте паралельне тренування.
        log(event="run_check_failed", error=str(e), decision="пропускаю цикл")
        return True


def trigger(digest):
    if not GH_TOKEN:
        log(event="would_trigger", dataset=DATASET_NAME, digest=digest,
            hint="GITHUB_TOKEN не заданий; див. docs/11")
        TRIGGERS.labels(result="dry_run").inc()
        return
    body = json.dumps({"event_type": EVENT_TYPE,
                       "client_payload": {"dataset": DATASET_NAME, "digest": digest}}).encode()
    try:
        status, _ = _gh("/dispatches", method="POST", body=body)
        log(event="triggered", dataset=DATASET_NAME, digest=digest, status=status)
        TRIGGERS.labels(result="ok").inc()
    except urllib.error.HTTPError as e:
        log(event="trigger_failed", status=e.code, body=e.read(300).decode("utf-8", "replace"))
        TRIGGERS.labels(result="error").inc()
    except Exception as e:  # noqa: BLE001
        log(event="trigger_failed", error=str(e))
        TRIGGERS.labels(result="error").inc()


def reconcile(reason):
    """Один цикл: порівняти стан і, якщо треба, запустити тренування."""
    LAST_RUN.set(time.time())
    try:
        have, size = storage_digest()
    except Exception as e:  # noqa: BLE001
        log(event="storage_unavailable", uri=DATASET_URI, error=str(e))
        RECONCILES.labels(result="storage_error").inc()
        return

    prod = champion_digest()
    DRIFTED.set(0 if have == prod else 1)

    if already_trained(have):
        RECONCILES.labels(result="in_sync").inc()
        log(event="in_sync", reason=reason, digest=have, champion=prod,
            detail=("прод на цих даних" if have == prod
                    else "на цих даних уже тренувались, gate не пропустив"))
        return

    log(event="out_of_sync", reason=reason, storage=have, champion=prod, bytes=size)

    if training_running():
        # Не помилка: цикл повториться через RESYNC і побачить той самий стан.
        # Саме тому level-triggered надійніший — «пропустили» не означає «втратили».
        RECONCILES.labels(result="already_running").inc()
        log(event="skipped", reason="тренування вже біжить")
        return

    RECONCILES.labels(result="triggered").inc()
    trigger(have)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if WEBHOOK_TOKEN and self.headers.get("Authorization", "").split()[-1:] != [WEBHOOK_TOKEN]:
            self.send_response(401); self.end_headers(); return
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        self.send_response(202); self.end_headers(); self.wfile.write(b"accepted")
        # Подія лише БУДИТЬ цикл. Вона не вирішує, тренувати чи ні — рішення
        # завжди ухвалює порівняння стану. Тому подія не може ні продублювати
        # тренування, ні запустити його на незмінних даних.
        _wake.set()

    def do_GET(self):
        if self.path == "/metrics":
            body = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def loop():
    while True:
        # Прокидаємось або за таймером, або від події — що станеться раніше.
        woken = _wake.wait(timeout=RESYNC)
        _wake.clear()
        try:
            reconcile("подія" if woken else "таймер")
        except Exception as e:  # noqa: BLE001
            log(event="reconcile_failed", error=str(e))
            RECONCILES.labels(result="error").inc()


if __name__ == "__main__":
    log(event="starting", dataset=DATASET_URI, model=f"{MODEL_NAME}@{MODEL_ALIAS}",
        resync_seconds=RESYNC, port=PORT,
        github_token="є" if GH_TOKEN else "НЕМАЄ (режим would_trigger)")
    threading.Thread(target=loop, daemon=True).start()
    HTTPServer(("", PORT), Handler).serve_forever()
