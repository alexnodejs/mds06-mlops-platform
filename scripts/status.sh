#!/usr/bin/env bash
# Що зараз працює. Викликається як `make status`.
set -uo pipefail
export AWS_PROFILE="${AWS_PROFILE:-goit-aws-mds}"

echo "── ноди ──"
kubectl get nodes --no-headers 2>/dev/null | awk '{printf "  %-46s %s\n",$1,$2}' || echo "  кластер недоступний"

echo
echo "── ArgoCD Application ──"
kubectl get application -n argocd 2>/dev/null | tail -n +2 \
  | awk '{printf "  %-16s %-10s %s\n",$1,$2,$3}' || echo "  немає"

echo
echo "── поди не в kube-system ──"
kubectl get pods -A --no-headers 2>/dev/null | grep -v "^kube-system" \
  | awk '{printf "  %-12s %-42s %-8s %s\n",$1,$2,$3,$4}'

echo
echo "── поди, що НЕ Running або READY не повний ──"
# ⚠️ Дивитись саме READY, а не STATUS: под зі статусом Running і READY 1/3
# означає, що сайдкари вбито по OOM. STATUS цього не покаже.
kubectl get pods -A --no-headers 2>/dev/null \
  | awk '$3!="Running" || $2!~/^([0-9]+)\/\1$/ {printf "  ⚠️  %-12s %-42s %-8s %s\n",$1,$2,$3,$4}' \
  | grep -v Completed || echo "  ✅ усі здорові"

echo
echo "── PVC ──"
kubectl get pvc -A --no-headers 2>/dev/null | awk '{printf "  %-10s %-24s %-9s %s\n",$1,$2,$3,$5}' || true

echo
echo "── тунелі ──"
pgrep -fl "kubectl port-forward" 2>/dev/null | sed 's/.*port-forward/  /' || echo "  жодного (make ports)"
