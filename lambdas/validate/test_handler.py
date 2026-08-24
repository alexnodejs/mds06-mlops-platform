"""Самоперевірка валідатора параметрів. Мережі не потребує:

    python lambdas/validate/test_handler.py

Найважливіше тут — не «відхиляє сміття» (це видно з коду), а те, що згенеровані
імена Job проходять RFC 1123. Kubernetes відмовить в обʼєкті з великою літерою
або підкресленням, і помилка буде далеко від причини.
"""
import re
from handler import handler


class Ctx:
    aws_request_id = "7f3a9b21-1111-2222-3333-444455556666"


RFC1123 = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?")

ok = handler({"commit_sha": "a1b2c3d4e5f6a7b8", "n_estimators": "50,100,200",
              "max_depth": "2,none", "experiment": "iris-rf"}, Ctx())
assert ok["runs"] == 6, ok
assert ok["grid_max_depth"] == "2,none", ok
for key in ("job_name", "promote_job_name"):
    assert RFC1123.fullmatch(ok[key]) and len(ok[key]) <= 63, (key, ok[key])

# Значення за замовчуванням: з CI може прийти лише commit_sha.
d = handler({"commit_sha": "0123456"}, Ctx())
assert d["grid_n_estimators"] == "50,100,200" and d["experiment"] == "iris-rf", d
assert d["dataset_uri"] == "s3://datasets/iris/v2.csv", d

# Тема 11: коротка назва версії розгортається в URI власного бакета.
assert handler({"commit_sha": "0123456", "dataset": "v3"}, Ctx())["dataset_uri"] \
    == "s3://datasets/iris/v3.csv"
assert handler({"commit_sha": "0123456",
                "dataset": "s3://datasets/iris/custom.csv"}, Ctx())["dataset_uri"] \
    == "s3://datasets/iris/custom.csv"

# Кожен рядок нижче має ВПАСТИ — інакше сміття доїде до кластера.
BAD = [
    ({"commit_sha": "нісенітниця"}, "не SHA"),
    ({"commit_sha": "abc"}, "закоротке SHA"),
    ({"commit_sha": "a1b2c3d", "n_estimators": "сто"}, "не число"),
    ({"commit_sha": "a1b2c3d", "n_estimators": "0"}, "поза межами"),
    ({"commit_sha": "a1b2c3d", "n_estimators": "99999"}, "поза межами"),
    ({"commit_sha": "a1b2c3d", "max_depth": "глибоко"}, "не число і не none"),
    ({"commit_sha": "a1b2c3d", "n_estimators": "1,2,3,4", "max_depth": "1,2,3,4,5"}, "сітка 20 запусків"),
    ({"commit_sha": "a1b2c3d", "experiment": "з пробілом"}, "небезпечне ім'я"),
    ({"commit_sha": "a1b2c3d", "experiment": "../../etc"}, "слеші в імені"),
    # Датасет: параметр із CI не має ставати способом читати чуже сховище.
    ({"commit_sha": "a1b2c3d", "dataset": "../etc/passwd"}, "шлях замість версії"),
    ({"commit_sha": "a1b2c3d", "dataset": "s3://someone-else/x.csv"}, "чужий бакет"),
    ({"commit_sha": "a1b2c3d", "dataset": "v9999"}, "версія поза формою"),
]
for payload, why in BAD:
    try:
        handler(payload, Ctx())
        raise AssertionError(f"мав відхилити ({why}): {payload}")
    except ValueError:
        pass

print(f"✅ валідатор: {len(BAD) + 6} перевірок пройдено")
