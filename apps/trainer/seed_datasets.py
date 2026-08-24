"""Кладе навчальні датасети в MinIO. Тема 11, слайд 16 («модель ← дані»).

    python seed_datasets.py

Запускається як разовий Job (`make seed`) і на кожному `make up`: бакет живе на
PVC, а PVC гине разом зі стеком, тож сейдинг має бути ІДЕМПОТЕНТНИМ і дешевим.

ЧОМУ ТРИ ВЕРСІЇ, А НЕ ОДИН ФАЙЛ
────────────────────────────────
До Теми 11 тренування брало `load_iris()` прямо з пакета sklearn. Це давало
150 рядків, з яких 30 ішли в тест — і f1 фізично міг набувати лише трьох
значень: 0.8997, 0.9333, 0.9666 (три, дві, одна помилка з 30). Крок 0.033 при
порозі MIN_DELTA=0.001 означав, що quality gate Теми 10 порівнював ШУМ.

    v1  150 рядків   оригінальний Iris — базова лінія, «як було»
    v2 1500 рядків   ресемплінг + гаусів шум; тест 300 рядків, крок f1 ~0.003
    v3 1500 рядків   те саме, але petal_length зсунуто — дані «поїхали»

v3 існує заради єдиної демонстрації, якої в курсі досі не було: перетренували на
зсунутих даних → метрики впали → gate відхилив. Уперше він судить РЕАЛЬНУ зміну
даних, а не випадковість на 30 рядках.

ДЕТЕРМІНОВАНІСТЬ обов'язкова: digest файла потрапляє в теги версії моделі
(`dataset_digest`). Якби генератор давав щоразу інші байти, digest мінявся б без
зміни змісту, і питання «це той самий датасет?» втратило б сенс.
"""

import hashlib
import io
import json
import os

import boto3
import numpy as np
import pandas as pd
from botocore.exceptions import ClientError
from sklearn.datasets import load_iris

BUCKET = os.getenv("DATASET_BUCKET", "datasets")
PREFIX = os.getenv("DATASET_PREFIX", "iris")
# Той самий seed, що RANDOM_STATE у train.py — щоб «42» у курсі означало
# одне й те саме число в усіх місцях.
SEED = int(os.getenv("SEED", "42"))
# Скільки рядків у збільшених версіях. 1500 обрано не навмання: тест 20% дає
# 300 рядків, тобто крок f1 ≈ 0.0033 — на порядок менший за поріг промоції.
BIG = int(os.getenv("BIG_ROWS", "1500"))
# На скільки зсунути petal_length у v3. 0.8 — той самий зсув, що DRIFT_SHIFT у
# генераторі трафіку Теми 9, щоб студент упізнав число.
SHIFT = float(os.getenv("V3_SHIFT", "0.8"))

# 🔴 РОЗМІР ШУМУ — НАЙВАЖЛИВІШЕ ЧИСЛО В ЦЬОМУ ФАЙЛІ, і воно зміряне, а не взяте
# зі стелі. Перша версія мала 0.05 (5% від внутрішньокласового std) — і давала
# f1 = 1.0000, тобто 300 з 300 правильних.
#
# Причина не в тому, що модель хороша. Ресемплінг іде З ПОВЕРНЕННЯМ: той самий
# оригінальний рядок потрапляє в набір по кілька разів, і після train_test_split
# його копії опиняються І в train, І в test. Це витік — модель не класифікує, а
# впізнає рядок, який уже бачила. Крихітний шум цього не рятує: 0.025 см при
# міжкласовій відстані в сантиметри — це той самий рядок.
#
# 0.5 від внутрішньокласового std дає реальне перекриття versicolor/virginica,
# тобто задачу, яку не можна розвʼязати ідеально. Метрика перестає впиратись у
# стелю, і quality gate знову має що порівнювати.
NOISE = float(os.getenv("NOISE_STD_FRAC", "0.5"))

FEATURES = ["sepal_length", "sepal_width", "petal_length", "petal_width"]


def log(**fields) -> None:
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)


def _base() -> pd.DataFrame:
    """Оригінальний Iris із наших іменами колонок.

    Імена snake_case, а не «sepal length (cm)» зі sklearn: рівно ці ключі
    приймає POST /predict і рівно їх шукає дріфт-експортер. Датасет мусить
    говорити мовою контракту API, а не мовою бібліотеки.
    """
    data = load_iris()
    df = pd.DataFrame(data.data, columns=FEATURES)
    df["target"] = [data.target_names[i] for i in data.target]
    return df


def _augment(df: pd.DataFrame, rows: int, shift: float, rng) -> pd.DataFrame:
    """Ресемплінг зі стратифікацією + гаусів шум.

    Шум ПРОПОРЦІЙНИЙ розкиду кожної ознаки, а не однакова константа:
    petal_length змінюється в межах ~6 см, а sepal_width — ~2, і однаковий шум
    спотворив би їх по-різному. Розмір — див. коментар до NOISE вище: від нього
    залежить, чи задача взагалі розвʼязна не ідеально.

    Стратифікація по класах: без неї ресемплінг випадково перекосив би баланс,
    і падіння метрик у v3 неможливо було б відрізнити від дисбалансу.
    """
    per_class = rows // df["target"].nunique()
    parts = []
    for cls, grp in df.groupby("target", sort=True):
        idx = rng.integers(0, len(grp), size=per_class)
        part = grp.iloc[idx].copy()
        for f in FEATURES:
            part[f] = part[f] + rng.normal(0, grp[f].std() * NOISE, size=per_class)
        parts.append(part)

    out = pd.concat(parts, ignore_index=True)
    if shift:
        # Зсуваємо ОДНУ ознаку — саме так виглядає реальна зміна на джерелі:
        # перекалібрували прилад, змінили одиниці, оновили парсер.
        out["petal_length"] = out["petal_length"] + shift

    # Від'ємних розмірів пелюсток не буває; шум міг завести туди хвіст.
    for f in FEATURES:
        out[f] = out[f].clip(lower=0.1).round(2)

    # Перемішуємо: інакше файл лежить блоками по класах, і будь-який наївний
    # head(100) дав би один клас.
    return out.sample(frac=1, random_state=SEED).reset_index(drop=True)


def build() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    base = _base()
    return {
        "v1.csv": base,
        "v2.csv": _augment(base, BIG, 0.0, rng),
        "v3.csv": _augment(base, BIG, SHIFT, rng),
    }


def s3_client():
    """Клієнт S3, який дивиться на MinIO, а не на справжній AWS.

    🔴 `boto3.client("s3")` БЕЗ endpoint_url піде в AWS S3, навіть коли в поді
    стоїть MLFLOW_S3_ENDPOINT_URL. Ця змінна — власність MLflow: він читає її
    сам і передає в boto3 параметром. Сам botocore про неї не знає нічого.
    Результат мовчазний і дорогий: ключі `minioadmin` летять в AWS, звідти
    прилітає 403, і виглядає це як «зламався MinIO».

    Тому endpoint передаємо ЯВНО. Порожня змінна = працюємо зі справжнім S3
    (так само поводиться train.py), тож локальний прогін теж можливий.
    """
    endpoint = os.getenv("MLFLOW_S3_ENDPOINT_URL") or None
    return boto3.client("s3", endpoint_url=endpoint)


def main() -> None:
    s3 = s3_client()

    # Бакет створює post-install Job чарта MinIO. Але `make seed` можуть
    # запустити раніше, ніж той відпрацює, тож створюємо ідемпотентно самі.
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)
        log(event="bucket_created", bucket=BUCKET)

    for name, df in build().items():
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        body = buf.getvalue().encode()
        key = f"{PREFIX}/{name}"
        digest = hashlib.sha256(body).hexdigest()[:12]

        s3.put_object(Bucket=BUCKET, Key=key, Body=body,
                      ContentType="text/csv",
                      # Метадані на самому об'єкті: видно в консолі MinIO, і
                      # їх можна звірити з тегом dataset_digest версії моделі.
                      Metadata={"rows": str(len(df)), "sha256-12": digest})
        log(event="uploaded", uri=f"s3://{BUCKET}/{key}",
            rows=len(df), bytes=len(body), digest=digest)

    log(event="seed_finished", bucket=BUCKET, prefix=PREFIX)


if __name__ == "__main__":
    main()
