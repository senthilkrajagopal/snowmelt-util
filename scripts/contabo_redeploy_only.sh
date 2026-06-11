#!/usr/bin/env bash
# All 3 images (snowmelt-app/datagen/ui) are already built + imported.
# This does ONLY the clean reinstall: uninstall both → delete ALL pvc → install both.
set -euo pipefail
cd /root/snowmelt || exit 1
UTIL="${SNOWMELT_UTIL:-/root/snowmelt-util}"   # this repo, checked out beside snowmelt
LOG=/root/deploy.log
: > "$LOG"
exec >"$LOG" 2>&1
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo "===== REDEPLOY-ONLY START $(date -u) ====="
echo "----- images present -----"
k3s ctr -n k8s.io images ls | grep -oE 'docker.io/library/snowmelt-(app|datagen|ui):latest' | sort -u

helm uninstall snowmelt-extras 2>/dev/null || true
helm uninstall snowmelt        2>/dev/null || true
echo "----- wait for pods to terminate -----"
kubectl wait --for=delete pod -l app.kubernetes.io/instance=snowmelt        --timeout=180s 2>/dev/null || true
kubectl wait --for=delete pod -l app.kubernetes.io/instance=snowmelt-extras --timeout=60s  2>/dev/null || true
sleep 8
echo "----- delete ALL pvc -----"
kubectl delete pvc --all --timeout=180s || true
echo "----- remaining pvc (should be empty) -----"
kubectl get pvc

echo "----- install snowmelt ($(date -u)) -----"
helm install snowmelt ./charts/snowmelt -f "$UTIL/deploy/contabo-values.yaml" || { echo "INSTALL snowmelt FAILED"; exit 21; }
echo "----- install snowmelt-extras ($(date -u)) -----"
helm install snowmelt-extras "$UTIL/charts/snowmelt-extras" -f "$UTIL/deploy/contabo-extras-values.yaml" || { echo "INSTALL extras FAILED"; exit 22; }

echo "===== DEPLOY COMPLETE $(date -u) ====="
kubectl get pods -o wide
echo "===== END $(date -u) ====="
