"""Одне визначення того, ЯК ми читаємо датасет і що вважаємо його відбитком.

НАВІЩО ОКРЕМИЙ МОДУЛЬ. Читання зі сховища встигло розповзтися по чотирьох
файлах — тренер, сейдер, дріфт-експортер, узгоджувач, — і в кожному та сама
пастка з endpoint_url. Гірше: «digest» означав різне в різних місцях.

🔴 Це вже вистрілило. Тег dataset_digest у версії моделі містив ВНУТРІШНІЙ хеш
MLflow (823bcf78), а узгоджувач рахував sha256 файла (3df7673d59e0). Два різні
алгоритми, які не збіглися б ніколи — узгоджувач вважав би дані вічно
розсинхронізованими і запускав тренування на кожному циклі. Нескінченний цикл
перетренування, який виглядав би як «система працює».

Тому відбиток тут рівно один: sha256 БАЙТІВ файла, перші 12 символів.
Не хеш DataFrame, не хеш MLflow, не метадані обʼєкта (їх ставить той, хто
заливав, і поставити можна будь-які). Байти підробити не можна.

Внутрішній digest MLflow нікуди не подівся — він і далі живе в
`run.inputs.dataset_inputs` завдяки mlflow.log_input. Це різні відповіді на
різні питання: MLflow відповідає «які дані бачив цей запуск», наш тег —
«який рівно файл лежав у сховищі».
"""

import hashlib
import io
import os

import boto3
import pandas as pd


def sha12(body: bytes) -> str:
    """Канонічний відбиток датасету. Одне визначення на весь репозиторій."""
    return hashlib.sha256(body).hexdigest()[:12]


def s3_client():
    """Клієнт, який дивиться на MinIO, а не на справжній AWS.

    🔴 endpoint_url ПЕРЕДАЄМО ЯВНО. MLFLOW_S3_ENDPOINT_URL — змінна MLflow, а не
    botocore: MLflow читає її сам і передає в boto3 параметром. Сам botocore про
    неї не знає нічого. `boto3.client("s3")` без endpoint_url піде у СПРАВЖНІЙ
    AWS S3 із ключами minioadmin, отримає 403 — і виглядатиме це як «зламався
    MinIO». Порожня змінна = працюємо зі справжнім S3, і це теж робочий режим.
    """
    return boto3.client("s3", endpoint_url=os.getenv("MLFLOW_S3_ENDPOINT_URL") or None)


def fetch(uri: str) -> tuple[bytes, str]:
    """s3://bucket/key -> (байти, відбиток)."""
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    body = s3_client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return body, sha12(body)


def read_csv(uri: str) -> tuple[pd.DataFrame, str]:
    """s3://bucket/key -> (DataFrame, відбиток БАЙТІВ, а не рядків).

    Відбиток рахуємо до розбору CSV: pandas може мовчки нормалізувати пробіли
    чи типи, і два різні файли дали б однаковий DataFrame. Питання «це той
    самий файл?» стосується файла, а не того, що з нього вийшло.
    """
    body, digest = fetch(uri)
    return pd.read_csv(io.BytesIO(body)), digest
