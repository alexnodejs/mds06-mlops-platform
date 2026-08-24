#!/usr/bin/env bash
# Blue-Green: підняти тіньовий варіант, показати різницю, прибрати.
# Тема 11, слайд 35.
#
#   make bluegreen-up     підняти green + увімкнути тіньовий трафік
#   make bluegreen-down   вимкнути тінь і прибрати green
#   make bluegreen        показати, що зараз де
#
# ⚠️ ПЕРЕМИКАННЯ ТРАФІКУ ЦЕЙ СКРИПТ НЕ РОБИТЬ — і це навмисно.
# Селектор Service живе в Git, і міняти його треба комітом: ArgoCD із selfHeal
# відкотить `kubectl patch` за кілька секунд. Саме це і є демонстрація.

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
NS=ml-demo
GREEN_SVC="http://ml-model-green.${NS}.svc.cluster.local/predict"

status() {
  echo "── куди йде бойовий трафік ──"
  kubectl -n "$NS" get svc ml-model -o jsonpath='{.spec.selector}{"\n"}' 2>/dev/null | sed 's/^/  Service ml-model selector: /'
  echo
  echo "── поди варіантів ──"
  kubectl -n "$NS" get pods -L variant --no-headers 2>/dev/null \
    | grep -E "ml-model" | awk '{printf "  %-34s %-6s %-9s variant=%s\n",$1,$2,$3,$6}'
  echo
  echo "── яка модель у якому варіанті ──"
  for v in blue green; do
    ip=$(kubectl -n "$NS" get pods -l "variant=$v" -o jsonpath='{.items[0].status.podIP}' 2>/dev/null)
    [[ -z "$ip" ]] && { printf "  %-6s —\n" "$v"; continue; }
    out=$(kubectl -n "$NS" exec deploy/load-generator -- \
      python3 -c "import urllib.request,json;print(urllib.request.urlopen('http://$ip:8000/healthz',timeout=5).read().decode())" 2>/dev/null)
    printf "  %-6s %s\n" "$v" "${out:-недоступний}"
  done
  echo
  echo "── тіньовий трафік ──"
  kubectl -n "$NS" get deploy load-generator -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="SHADOW_URL")].value}' 2>/dev/null \
    | grep -q . && echo "  увімкнено" || echo "  вимкнено"
}

case "${1:-status}" in
  up)
    echo "── піднімаю green ──"
    kubectl -n "$NS" scale deploy/ml-model-green --replicas=1
    kubectl -n "$NS" rollout status deploy/ml-model-green --timeout=120s
    echo
    echo "── вмикаю тіньовий трафік (той самий payload в обидва варіанти) ──"
    kubectl -n "$NS" set env deploy/load-generator SHADOW_URL="$GREEN_SVC"
    kubectl -n "$NS" rollout status deploy/load-generator --timeout=120s
    echo
    status
    cat <<'EOF'

  Далі:
    Grafana -> «ML-модель — моніторинг», панель «Blue vs Green»
    Через ~1 хв зʼявляться дві лінії: метрики розділені лейблом variant.

  ⚠️ Якщо у green видно source="baked" — аліаса @challenger у реєстрі немає.
     Створити кандидата:  make train N=10 D=1 PROMOTE=false

  Перемкнути трафік — КОМІТОМ, не командою:
    k8s/model-api/service.yaml:  variant: blue  ->  green
    git commit -am "blue-green: перемикаємо на green" && git push
EOF
    ;;
  down)
    kubectl -n "$NS" set env deploy/load-generator SHADOW_URL- >/dev/null
    kubectl -n "$NS" scale deploy/ml-model-green --replicas=0
    echo "✅ тінь вимкнено, green прибрано (слот пода звільнено)"
    ;;
  *) status ;;
esac
