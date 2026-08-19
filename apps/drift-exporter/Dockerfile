# НЕОБОВ'ЯЗКОВИЙ легкий образ ЛИШЕ дріфт-експортера (без mlflow, boto3,
# pandas, matplotlib): ~330 MiB замість ~1.1 GiB. Сенс — под експортера
# працює 24/7, а MLflow йому не потрібен ні на секунду.
#
# КОНТРАКТНИЙ образ Теми 9 — це train/Dockerfile (mds06-mlflow-tools:v1), він
# містить обидва скрипти. Цей файл беріть, лише якщо не хочете тягнути на ноду
# гігабайт заради 300 рядків scipy:
#   docker buildx build --platform linux/amd64 \
#     -t 832828869208.dkr.ecr.eu-central-1.amazonaws.com/mds06-drift-exporter:v1 --push .
#   (контекст — сама тека drift/)
#
# --platform linux/amd64 ОБОВ'ЯЗКОВИЙ: мак ARM, ноди EKS x86_64.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# test_drift.py кладемо в образ навмисно: самоперевірку можна прогнати ПРЯМО
# в кластері одним kubectl exec — не треба відтворювати venv локально.
#   kubectl -n mlflow exec deploy/drift-exporter -- python test_drift.py
COPY drift_exporter.py test_drift.py ./

# 9100 — той самий порт, що в Service і ServiceMonitor (порт name: http).
EXPOSE 9100

# Числом, а не імʼям: інакше runAsNonRoot: true валить под з
# "container has non-numeric user".
USER 1000

CMD ["python", "drift_exporter.py"]
