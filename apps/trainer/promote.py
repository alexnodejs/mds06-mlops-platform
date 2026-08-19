"""Промоція моделі: перевісити аліас @champion і сказати сервісу перечитати.

Запускається як Job у кластері КРОКОМ PromoteModel пайплайну Теми 10 — і
тільки якщо quality gate вирішив, що нова модель краща.

ЧОМУ ЦЕ ПОД У КЛАСТЕРІ, А НЕ LAMBDA. Тут потрібні дві речі, яких у Lambda
немає: MLflow (ClusterIP mlflow.mlflow.svc) і сервіс моделі
(ClusterIP ml-model.ml-demo.svc). Обидві адреси існують лише всередині
кластера. Той самий образ, що тренує, — жодної нової збірки.

    MODEL_VERSION=5 python promote.py

Ідемпотентний: повторний запуск із тією ж версією нічого не зламає.
"""

import json
import os
import sys
import urllib.error
import urllib.request

import mlflow

MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", "iris-rf")
ALIAS = os.getenv("MODEL_ALIAS", "champion")
VERSION = os.getenv("MODEL_VERSION", "").strip()
# Сервіс моделі з Теми 8. Ендпоінт /reload змушує його піти в реєстр і
# перечитати модель за аліасом, не чекаючи чергового опитування (30 c).
RELOAD_URL = os.getenv("RELOAD_URL", "http://ml-model.ml-demo.svc.cluster.local/reload")


def log(**fields):
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)


def main() -> int:
    if not VERSION.isdigit():
        log(event="promote_failed", error=f"MODEL_VERSION=«{VERSION}» — треба число")
        return 1

    client = mlflow.MlflowClient()
    previous = None
    try:
        previous = client.get_model_version_by_alias(MODEL_NAME, ALIAS).version
    except Exception:  # noqa: BLE001
        pass  # аліаса ще немає — нормально для першої моделі

    client.set_registered_model_alias(MODEL_NAME, ALIAS, VERSION)
    log(event="alias_moved", model=MODEL_NAME, alias=ALIAS,
        version=VERSION, previous=previous)

    # Сервіс моделі однаково підхопить нову версію сам протягом
    # MODEL_RELOAD_SECONDS. /reload — щоб на занятті не чекати.
    # Помилка тут НЕ валить промоцію: аліас уже переставлено, і це головне.
    # Сервіс міг бути не піднятий узагалі — це не привід відкочувати реєстр.
    try:
        req = urllib.request.Request(RELOAD_URL, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(event="reloaded", status=resp.status, body=resp.read(400).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as e:
        log(event="reload_skipped", error=str(e),
            hint="сервіс моделі недоступний; він перечитає реєстр сам за ~30 c")

    return 0


if __name__ == "__main__":
    sys.exit(main())
