#!/usr/bin/env bash
# DEMO MODE on Contabo: full product demo.
#   quickwit ON, otel-logs-collector ON, otel-demo INSTALLED, datagen = 1 light pod.
# Trigger: "deploy demo mode on contabo server".
set -uo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
SM=/root/snowmelt/charts/snowmelt
EX=/root/snowmelt-util/charts/snowmelt-extras
DEMO=/root/snowmelt-util/charts/snowmelt-demo
DEP=/root/snowmelt-util/deploy
echo "===== DEMO MODE $(date -u) ====="

echo "--- snowmelt: quickwit + log-collector ON ---"
helm upgrade snowmelt "$SM" -f "$DEP/contabo-values.yaml" \
  --set quickwit.enabled=true --set otelLogsCollector.enabled=true \
  -n default --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error"

echo "--- otel-demo: install/upgrade in ns demo ---"
helm upgrade --install snowmelt-demo "$DEMO" -f "$DEP/contabo-demo-values.yaml" \
  -n demo --create-namespace --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error"

echo "--- datagen: 1 light pod (wellformed only, no firehose) ---"
helm upgrade --install snowmelt-extras "$EX" -f "$DEP/contabo-extras-demo.yaml" \
  -n default --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error"

echo "===== DEMO MODE applied $(date -u) ====="
