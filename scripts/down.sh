#!/usr/bin/env bash
# Зносить стек, лишаючи кластер і ArgoCD. Викликається як `make down`.
#
# Видаляємо ОДИН ресурс — root. Його фіналайзер каскадом зносить десять
# дочірніх Application, а їхні фіналайзери — усе, що вони створили.
# Раніше тут був список із десяти імен, який розходився з реальністю щоразу,
# коли додавали новий Application.

set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "── root Application (каскад: root -> дочірні -> їхні ресурси) ──"
kubectl delete -f "$HERE/argocd/root.yaml" --ignore-not-found --timeout=300s

# Підстраховка: якщо root уже було видалено раніше, дочірні лишились сиротами.
LEFT=$(kubectl get application -n argocd --no-headers 2>/dev/null | wc -l | tr -d ' ')
if [[ "$LEFT" != "0" ]]; then
  echo "── лишилось $LEFT сиротних Application, прибираю ──"
  kubectl delete application --all -n argocd --timeout=300s
fi

echo "── namespace ──"
# namespace ArgoCD не видаляє: CreateNamespace=true створює, але не прибирає.
kubectl delete ns mlflow ml-demo monitoring logging demo-react \
  --ignore-not-found --timeout=300s

pkill -f "kubectl port-forward" 2>/dev/null
echo
echo "✅ знесено. Кластер і ArgoCD працюють."
echo "   Повернути все: make up"
echo "   Рахунок за кластер іде далі (~\$7/добу). Зупинити зовсім: make cluster-down"
