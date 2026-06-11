#!/usr/bin/env bash
# helm-upgrade both charts: quickwit OFF (frees logs-collector CPU) + datagen
# initialDelaySecs=120 so the sink re-resolves all snowmelt backends (fan-out fix).
# Sequence: upgrade snowmelt (restarts STS), upgrade extras, restart datagen.
set -euo pipefail
cd /root/snowmelt || exit 1
UTIL="${SNOWMELT_UTIL:-/root/snowmelt-util}"   # this repo, checked out beside snowmelt
LOG=/root/deploy.log; : > "$LOG"; exec >"$LOG" 2>&1
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo "===== UPGRADE START $(date -u) ====="

echo "----- helm upgrade snowmelt (quickwit off) -----"
helm upgrade snowmelt ./charts/snowmelt -f "$UTIL/deploy/contabo-values.yaml" || { echo "UPGRADE snowmelt FAILED"; exit 21; }
echo "----- helm upgrade snowmelt-extras (initialDelaySecs=120) -----"
helm upgrade snowmelt-extras "$UTIL/charts/snowmelt-extras" -f "$UTIL/deploy/contabo-extras-values.yaml" || { echo "UPGRADE extras FAILED"; exit 22; }

echo "----- restart datagen so it picks up new config + re-resolves backends -----"
kubectl rollout restart deploy/snowmelt-extras-datagen

echo "----- wait for snowmelt STS rollout (all 4 Ready) -----"
kubectl rollout status statefulset/snowmelt --timeout=300s || echo "(snowmelt rollout slow; continuing)"

echo "===== UPGRADE COMPLETE $(date -u) ====="
kubectl get pods -o wide | grep -E 'snowmelt-[0-9]|datagen|quickwit|otel-logs'
echo "===== END $(date -u) ====="
