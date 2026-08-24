"""ValidateParams — найдешевший крок пайплайну (слайд 26, lambda/validate.py).

НАВІЩО ВІН ПЕРШИЙ. Наступний крок піднімає под у Kubernetes і тренує модель.
Якщо з CI прилетіло n_estimators="сто", то без цієї Lambda ми дізнаємось про
це через дві хвилини — з логів пода, який упав на int(). З нею — за 200 мс,
до того, як витрачено хоч один ресурс. Це загальне правило пайплайнів:
найдешевша перевірка йде першою.

ВХІД (те, що GitHub Actions кладе в --input):
    {"commit_sha": "abc123...", "ref": "main",
     "n_estimators": "50,100,200", "max_depth": "2,none",
     "experiment": "iris-rf"}
Усі поля, крім commit_sha, необов'язкові — є розумні значення за замовчуванням.

ВИХІД — нормалізовані параметри, готові до підстановки в Job:
    {"commit_sha": ..., "short_sha": "abc123a", "job_name": "train-abc123a-...",
     "experiment": ..., "grid_n_estimators": "50,100,200", "grid_max_depth": "2,none"}

Помилка кидається як виняток: Step Functions ловить її через Catch і веде
виконання в стан ParamsRejected. Повертати {"ok": false} НЕ треба — тоді
довелося б перевіряти цей прапорець у кожному наступному кроці.
"""

import os
import re

# Межі свідомо широкі: задача цієї перевірки — відсікти СМІТТЯ (літери,
# від'ємні числа, порожні рядки), а не нав'язувати «правильні» гіперпараметри.
# Занадто вузькі межі перетворюють quality gate на перешкоду експерименту.
MAX_ESTIMATORS = 2000
MAX_DEPTH = 100
MAX_GRID = 12  # 12 запусків х ~4 c = ще прийнятно для заняття

# Ім'я експерименту їде в Kubernetes як значення env і в MLflow як ім'я.
# Дозволяємо лише те, що безпечно в обох: без пробілів, лапок і слешів.
SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")

# Тема 11: на якому датасеті тренувати. З CI приходить або коротка назва
# версії ("v3"), або повний URI. Коротку розгортаємо самі — щоб у workflow не
# доводилось писати s3://... і щоб не можна було послати под читати чужий бакет.
DATASET_BUCKET = os.getenv("DATASET_BUCKET", "datasets")
DATASET_PREFIX = os.getenv("DATASET_PREFIX", "iris")
SAFE_VERSION = re.compile(r"^v[0-9]{1,3}$")


def _ints(raw: str, field: str, hi: int) -> list[int]:
    out = []
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.lstrip("-").isdigit():
            raise ValueError(f"{field}: «{chunk}» не число")
        val = int(chunk)
        if not 1 <= val <= hi:
            raise ValueError(f"{field}: {val} поза межами 1..{hi}")
        out.append(val)
    if not out:
        raise ValueError(f"{field}: порожній список")
    return out


def handler(event, context):
    commit_sha = str(event.get("commit_sha", "")).strip()
    # 7 символів — мінімум, який git вважає однозначним скороченням.
    if not re.fullmatch(r"[0-9a-f]{7,40}", commit_sha):
        raise ValueError(f"commit_sha: «{commit_sha}» не схоже на git SHA")

    experiment = str(event.get("experiment") or "iris-rf").strip()
    if not SAFE_NAME.match(experiment):
        raise ValueError(f"experiment: «{experiment}» — дозволені лише [a-zA-Z0-9._-], до 63 символів")

    n_estimators = _ints(event.get("n_estimators") or "50,100,200", "n_estimators", MAX_ESTIMATORS)

    # max_depth окремо: тут дозволене слово "none" (дерево без обмеження глибини).
    depths: list[str] = []
    for chunk in str(event.get("max_depth") or "2,none").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.lower() in ("none", "null"):
            depths.append("none")
            continue
        if not chunk.isdigit() or not 1 <= int(chunk) <= MAX_DEPTH:
            raise ValueError(f"max_depth: «{chunk}» — треба число 1..{MAX_DEPTH} або none")
        depths.append(chunk)
    if not depths:
        raise ValueError("max_depth: порожній список")

    total = len(n_estimators) * len(depths)
    if total > MAX_GRID:
        raise ValueError(
            f"сітка {len(n_estimators)}x{len(depths)} = {total} запусків, "
            f"дозволено до {MAX_GRID}: заняття не має тривати довше за перерву"
        )

    # Датасет: або "v2", або повний s3://. Усе інше — відмова.
    raw_ds = str(event.get("dataset") or "v2").strip()
    if SAFE_VERSION.match(raw_ds):
        dataset_uri = f"s3://{DATASET_BUCKET}/{DATASET_PREFIX}/{raw_ds}.csv"
    elif raw_ds.startswith(f"s3://{DATASET_BUCKET}/"):
        # Повний URI приймаємо ЛИШЕ в межах свого бакета: інакше параметр із CI
        # став би способом змусити под читати довільне сховище.
        dataset_uri = raw_ds
    else:
        raise ValueError(
            f"dataset: «{raw_ds}» — треба або версія (v1, v2, v3), "
            f"або URI в межах s3://{DATASET_BUCKET}/"
        )

    short_sha = commit_sha[:7]

    # Унікальний суфікс — з aws_request_id виклику Lambda. Він унікальний на
    # кожен виклик і складається з [0-9a-f-], тобто гарантовано підходить під
    # RFC 1123, за яким Kubernetes перевіряє імена обʼєктів.
    #
    # Чому не $$.Execution.Name прямо в ASL: імʼя виконання Step Functions
    # дозволяє символи, яких Kubernetes не приймає (підкреслення, крапки,
    # верхній регістр). Достатньо одному студенту запустити виконання з іменем
    # "Test_1" — і Job не створиться, а помилка буде про «invalid value»,
    # у якій звʼязок із іменем виконання неочевидний.
    uniq = context.aws_request_id[:8] if context is not None else "local"

    return {
        "commit_sha": commit_sha,
        "short_sha": short_sha,
        "ref": str(event.get("ref") or "main"),
        "run_url": str(event.get("run_url") or ""),
        # Імена Job мусять бути унікальними: eks:runJob.sync не прибирає Job
        # за собою одразу, і другий запуск з тим самим іменем дав би 409
        # AlreadyExists. SHA коміта лишаємо в імені, щоб у `kubectl get job`
        # було видно, який коміт тренувався.
        "job_name": f"train-{short_sha}-{uniq}",
        "promote_job_name": f"promote-{short_sha}-{uniq}",
        "experiment": experiment,
        "dataset_uri": dataset_uri,
        "grid_n_estimators": ",".join(str(x) for x in n_estimators),
        "grid_max_depth": ",".join(depths),
        "runs": total,
    }
