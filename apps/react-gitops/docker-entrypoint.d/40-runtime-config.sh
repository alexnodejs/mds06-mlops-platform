#!/bin/sh
# Образ nginx:alpine виконує ВСІ скрипти з /docker-entrypoint.d/ перед стартом
# самого nginx. Це офіційна точка розширення — власний ENTRYPOINT не потрібен.
#
# Навіщо: React-бандл зібрано один раз і він однаковий скрізь. Але імʼя пода,
# ноди й namespace відомі лише в момент запуску контейнера. Тому ми генеруємо
# маленький config.js із того, що Kubernetes передав у змінні оточення
# (див. блок downward API у k8s/deployment.yaml).
#
# Результат: ОДИН образ працює в dev, stage і prod без перезбірки.

set -e

cat > /usr/share/nginx/html/config.js <<EOF
window.APP_INFO = {
  version: "${APP_VERSION:-dev}",
  pod: "${POD_NAME:-невідомо}",
  node: "${NODE_NAME:-невідомо}",
  namespace: "${POD_NAMESPACE:-невідомо}"
};
EOF

echo "[runtime-config] version=${APP_VERSION:-dev} pod=${POD_NAME:-невідомо}"
