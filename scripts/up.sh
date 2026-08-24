#!/usr/bin/env bash
# Піднімає ВЕСЬ стек (Теми 6, 8, 9) у наявний кластер із ArgoCD.
# Викликається як `make up`.
#
# Раніше це були два скрипти на 8 кроків із ручним `kubectl apply` кожного
# Application і зашитими паузами між «хвилями». Тепер порядок задає сам
# ArgoCD через sync-wave, а скрипт робить лише те, чого GitOps не може:
# створює Secret (у ньому паролі — у Git їм не місце) і чекає результату.

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PWFILE="$HOME/.mlflow-demo-credentials"

fail() { echo "❌ $1"; exit 1; }

echo "══ 1. Передумови ══"
kubectl get ns argocd >/dev/null 2>&1 || fail "немає namespace argocd — спершу встановіть ArgoCD (docs/06-deploy-methods.md)"
kubectl get sc gp3   >/dev/null 2>&1 || fail "немає StorageClass gp3 — kubectl apply -f deploy/0-storage/"
N=$(kubectl get nodes --no-headers | wc -l | tr -d ' ')
[[ "$N" -ge 3 ]] || echo "  ⚠️  нод лише $N; стек розрахований на 3"
echo "  ✅ ArgoCD, StorageClass gp3, нод: $N"

# ⭐ Найдорожча помилка на занятті: підняти стек, чекати 10 хвилин і лише тоді
# зрозуміти, що ArgoCD дивиться в репозиторій, якого не існує, або в гілку без
# останнього коміта. ArgoCD читає Git, а не вашу файлову систему — локальні
# зміни для нього не існують, доки не запушені.
REPO=$(grep -m1 'repoURL:' "$HERE/argocd/root.yaml" | awk '{print $2}')
git -C "$HERE" ls-remote "$REPO" HEAD >/dev/null 2>&1 \
  || fail "ArgoCD не зможе прочитати $REPO — репозиторій не існує, або він приватний і ArgoCD не має креденшелів"
LOCAL=$(git -C "$HERE" rev-parse HEAD 2>/dev/null)
REMOTE=$(git -C "$HERE" ls-remote "$REPO" HEAD 2>/dev/null | cut -f1)
if [[ -n "$LOCAL" && "$LOCAL" != "$REMOTE" ]]; then
  echo "  ⚠️  локальний HEAD (${LOCAL:0:7}) != віддалений (${REMOTE:0:7})"
  echo "      ArgoCD візьме ВІДДАЛЕНУ версію. Якщо це не те, чого ви хочете: git push"
fi
echo "  ✅ репозиторій доступний: $REPO"

echo
echo "══ 2. Secret mlflow-credentials ══"
# Паролі живуть у файлі під $HOME, а не в Git. Якщо файл є — перевикористовуємо,
# щоб логіни не мінялись між заняттями.
# Обидва namespace: у mlflow секрет потрібен MinIO, Postgres і самому MLflow;
# у ml-demo — сервісу моделі, щоб він міг СКАЧАТИ артефакт моделі з MinIO.
# Без нього в ml-demo сервіс мовчки лишається на моделі, зашитій в образ, і
# демонстрація «промоутнули версію — сервіс її підхопив» не працює.
for NS in mlflow ml-demo; do
  kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done
if [[ -f "$PWFILE" ]]; then
  # shellcheck disable=SC1090
  source "$PWFILE"; echo "  ↻ пароль узято з $PWFILE"
else
  MINIO_PW=$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)
  PG_PW=$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)
  printf 'MINIO_PW=%s\nPG_PW=%s\n' "$MINIO_PW" "$PG_PW" > "$PWFILE"
  chmod 600 "$PWFILE"; echo "  + новий пароль збережено у $PWFILE (chmod 600)"
fi
for NS in mlflow ml-demo; do
kubectl create secret generic mlflow-credentials -n "$NS" \
  --from-literal=rootUser=minioadmin \
  --from-literal=rootPassword="$MINIO_PW" \
  --from-literal=AWS_ACCESS_KEY_ID=minioadmin \
  --from-literal=AWS_SECRET_ACCESS_KEY="$MINIO_PW" \
  --from-literal=AWS_DEFAULT_REGION=eu-central-1 \
  --from-literal=POSTGRES_DB=mlflow \
  --from-literal=POSTGRES_USER=mlflow \
  --from-literal=POSTGRES_PASSWORD="$PG_PW" \
  --from-literal=POSTGRES_POSTGRES_PASSWORD="$PG_PW" \
  --from-literal=MLFLOW_S3_ENDPOINT_URL=http://minio.mlflow.svc.cluster.local:9000 \
  --from-literal=MLFLOW_TRACKING_URI=http://mlflow.mlflow.svc.cluster.local:80 \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done
echo "  ✅ 11 ключів у namespace mlflow і ml-demo"

echo
echo "══ 3. Root Application (він підтягне решту) ══"
kubectl apply -f "$HERE/argocd/root.yaml"
# Скільки дочірніх Application очікувати — рахуємо з файлів у Git, а не
# зашиваємо число: додали файл у argocd/apps/ — цикл сам це врахує.
WANT=$(ls "$HERE"/argocd/apps/app-*.yaml | wc -l | tr -d ' ')
echo "  чекаю $WANT застосунків; порядок задають sync-wave (0 -> 3)"
echo "  перший раз довше: Prometheus ставить свої CRD ~3 хв"

for i in $(seq 1 60); do
  sleep 10
  READY=$(kubectl get application -n argocd --no-headers 2>/dev/null | grep -c "Synced.*Healthy")
  printf "\r  [%3dс] Synced/Healthy: %s з %s   " $((i*10)) "$READY" "$WANT"
  [[ "$READY" -ge "$WANT" ]] && { echo; echo "  ✅ усі здорові"; break; }
  [[ $i -eq 60 ]] && { echo; echo "  ⚠️  не всі піднялись за 10 хв:"; kubectl get application -n argocd; }
done

echo
echo "══ 4. Датасети в MinIO (Тема 11) ══"
# Бакет живе на PVC, а PVC гине разом зі стеком — тому сейдинг на КОЖНОМУ
# підйомі, а не один раз. Генератор детермінований, повтор нічого не ламає.
"$HERE/scripts/seed.sh" || echo "  ⚠️  датасети не залились; тренування впаде на fallback у load_iris()"

echo
echo "══ 5. Генератор трафіку ══"
kubectl -n ml-demo scale deploy/load-generator --replicas=1 >/dev/null 2>&1 \
  && echo "  ✅ увімкнено (RPS=5, 5% битих запитів — без нього графіки порожні)" \
  || echo "  ⏭  ml-demo ще не готовий, увімкніть пізніше: make loadgen"

echo
echo "══ 6. Тренування — щоб MLflow не був порожній ══"
"$HERE/scripts/train.sh" || echo "  ⚠️  тренування не пройшло; MLflow працює, але порожній"

echo
"$HERE/scripts/ports.sh"
