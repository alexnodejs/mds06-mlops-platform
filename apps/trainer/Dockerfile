# КОНТРАКТНИЙ образ mds06-mlflow-tools:v1 — містить ОБА скрипти:
# train.py (Job тренування) і drift_exporter.py (Deployment експортера).
# Один образ на дві задачі, як вимагає контракт Теми 9.
#
# ЗБІРКА — з КОРЕНЯ репозиторію (контекст мусить бачити і apps/trainer/,
# і apps/drift-exporter/). Простіше: `make build-tools`.
#   docker buildx build --platform linux/amd64 -f apps/trainer/Dockerfile \
#     -t 832828869208.dkr.ecr.eu-central-1.amazonaws.com/mds06-mlflow-tools:v5 --push .
#
# --platform linux/amd64 ОБОВ'ЯЗКОВИЙ: мак розробника ARM, ноди EKS x86_64.
# Без нього под падає з "exec format error", і виглядає це як зламаний образ.
#
# Запуск у кластері:
#   Job тренування:      command: ["python", "train.py"]
#   Deployment дріфту:   command: ["python", "drift_exporter.py"]
#   обом достатньо      envFrom: [{secretRef: {name: mlflow-credentials}}]
FROM python:3.13-slim

# python:3.13-slim, а не 3.12: numpy 2.5.2 вимагає Python >= 3.12, а колеса
# cp313 manylinux x86_64 є в усіх важких пакетів. Alpine НЕ БРАТИ: там musl,
# manylinux-колеса не підходять, pip почне збирати scipy з сорсів (+40 хв).
#
# PYTHONUNBUFFERED — критично: у контейнері stdout не термінал, тож Python
# буферизує вивід блоками, і логи прилітали б ривками, а при падінні пода
# останній буфер губився б — саме ті рядки, заради яких ви й дивитесь логи.
#
# HOME=/tmp і MPLCONFIGDIR: під uid 1000 домашньої теки в образі немає, а
# matplotlib будує кеш шрифтів при першому імпорті. Без цих двох змінних
# перший запуск сипле UserWarning про недоступний кеш у кожен лог.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

# requirements окремим шаром ДО коду: правка скриптів не інвалідує кеш pip
# (а pip install mlflow+scipy+sklearn під QEMU — це основні 8-10 хв збірки).
# --no-cache-dir економить ~200 MiB кешу коліс.
COPY apps/trainer/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# apps/drift-exporter/requirements.txt тут НЕ потрібен: строга підмножина.
COPY apps/trainer/train.py apps/trainer/promote.py apps/trainer/seed_datasets.py \
     apps/drift-exporter/drift_exporter.py apps/drift-exporter/test_drift.py ./

EXPOSE 9100

# USER саме ЧИСЛОМ, а не імʼям. Пастка Kubernetes: із securityContext
# runAsNonRoot: true kubelet не може перевірити текстове імʼя і падає з
# "container has runAsNonRoot and image has non-numeric user".
USER 1000

# Дефолт — тренування; експортер запускають, перевизначивши command.
CMD ["python", "train.py"]
