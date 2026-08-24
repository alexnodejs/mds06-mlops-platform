# ОДНОЕТАПНА збірка — і це виміряно, а не вгадано: багатоетапний варіант
# того самого образу дав 404 MiB проти 414 MiB, тобто 4 MB різниці на pull (3%).
# Причина: багатоетапність прибирає ТУЛЧЕЙН ЗБІРКИ (gcc, -dev пакети, заголовки),
# а тут його немає — усі пакети ставляться з готових manylinux-коліс, жодної
# компіляції. Правило: багатоетапна збірка окуповується тоді і тільки тоді,
# коли у Dockerfile є apt-get install build-essential або збірка з сорсів.
#
# ЗБІРКА (мак на ARM, ноди EKS на x86_64 — без --platform под падає в exec format error):
#   docker buildx build --platform linux/amd64 -t <ecr>/mds06-ml-model:v6 --push .
FROM python:3.13-slim

# python:3.13-slim, а не 3.12: numpy 2.5.2 вимагає Python >= 3.12, а колеса
# cp313 manylinux x86_64 є в усіх трьох важких пакетів (numpy, scipy, sklearn).
# Alpine НЕ БРАТИ: там musl, manylinux-колеса не підходять, pip почне збирати
# scipy з сорсів — це +компілятор, ~40 хв збірки і образ БІЛЬШИЙ за slim.

# PYTHONUNBUFFERED — критично: коли stdout не термінал (а в контейнері він не
# термінал), Python буферизує вивід блоками по 4-8 КБ. Логи прилітали б ривками,
# а при падінні поду останній буфер губився б — тобто саме ті рядки, заради яких
# ви й дивитеся логи. Одна змінна закриває питання і для нашого коду, і для uvicorn.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# requirements окремим шаром ДО коду: правка app.py не інвалідує кеш pip.
# --no-cache-dir економить ~200 MiB кешу коліс — найбільший однорядковий виграш.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY train.py app.py ./

# Тренування ПІД ЧАС ЗБІРКИ: у рантаймі немає ні мережі, ні тому для запису
# (сховище кластера зламане — будь-який PVC зависне в Pending назавжди).
# Результат — model.pkl (~174 KiB) усередині образу.
RUN python train.py

EXPOSE 8000

# USER саме ЧИСЛОМ, а не імʼям. Пастка Kubernetes: із securityContext
# runAsNonRoot: true kubelet не може перевірити текстове імʼя користувача і
# падає з "container has runAsNonRoot and image has non-numeric user".
# uid 1000 у debian-slim вільний, файли в /app читабельні (umask 022).
USER 1000

# Один воркер: горизонтально масштабуємось реплікáми Deployment, а не
# воркерами всередині пода — інакше HPA і requests/limits рахують не те,
# та й кожен воркер тримав би власний лічильник Prometheus.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
