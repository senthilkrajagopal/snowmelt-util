#!/usr/bin/env bash
# STRESS MODE on Contabo: max ingest throughput.
#   quickwit OFF, otel-logs-collector OFF, otel-demo UNINSTALLED, datagen FIREHOSE.
# Trigger: "deploy stress mode on contabo server".
set -uo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
SM=/root/snowmelt/charts/snowmelt
EX=/root/snowmelt-util/charts/snowmelt-extras
DEP=/root/snowmelt-util/deploy
echo "===== STRESS MODE $(date -u) ====="

echo "--- snowmelt: quickwit + log-collector OFF (headless collector stays) ---"
helm upgrade snowmelt "$SM" -f "$DEP/contabo-values.yaml" \
  --set quickwit.enabled=false --set otelLogsCollector.enabled=false \
  -n default --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error"

echo "--- otel-demo: uninstall (frees the demo namespace) ---"
helm uninstall snowmelt-demo -n demo 2>&1 | tail -1 || true

echo "--- datagen: firehose (4 pods × 12500 = 50k dp/s; batch 1000) ---"
helm upgrade --install snowmelt-extras "$EX" -f "$DEP/contabo-extras-stress.yaml" \
  -n default --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error"

echo "===== STRESS MODE applied $(date -u) ====="
kubectl get pods -n default 2>/dev/null | grep -cE "datagen.*Running" | xargs echo "datagen running:"
