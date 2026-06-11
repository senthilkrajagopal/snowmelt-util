#!/usr/bin/env bash
# Finish the Contabo clean redeploy: app + datagen images are already imported;
# this builds the UI image (context=ui/) then does the clean reinstall.
set -euo pipefail
cd /root/snowmelt || exit 1
UTIL="${SNOWMELT_UTIL:-/root/snowmelt-util}"   # this repo, checked out beside snowmelt
LOG=/root/deploy.log
: > "$LOG"
exec >"$LOG" 2>&1
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo "===== FINISH DEPLOY START $(date -u) ====="

echo "----- build snowmelt-ui (context=ui/) ($(date -u)) -----"
docker build -f ui/Dockerfile -t snowmelt-ui:latest ui/ || { echo "BUILD FAILED: snowmelt-ui"; exit 11; }
docker save snowmelt-ui:latest | k3s ctr -n k8s.io images import - || { echo "IMPORT FAILED: snowmelt-ui"; exit 12; }

echo "----- verify all 3 images present -----"
k3s ctr -n k8s.io images ls | grep -oE 'docker.io/library/snowmelt-(app|datagen|ui):latest' | sort -u
for img in app datagen ui; do
  k3s ctr -n k8s.io images ls | grep -q "snowmelt-${img}:latest" || { echo "MISSING IMAGE: snowmelt-${img}"; exit 13; }
done

echo "===== CLEAN REDEPLOY ($(date -u)) ====="
helm uninstall snowmelt-extras 2>/dev/null || true
helm uninstall snowmelt        2>/dev/null || true
echo "----- waiting for pods to terminate -----"
kubectl wait --for=delete pod -l app.kubernetes.io/instance=snowmelt        --timeout=150s 2>/dev/null || true
kubectl wait --for=delete pod -l app.kubernetes.io/instance=snowmelt-extras --timeout=60s  2>/dev/null || true
sleep 5
echo "----- delete ALL pvc -----"
kubectl delete pvc --all --timeout=150s || true
echo "----- remaining pvc (should be empty) -----"
kubectl get pvc

echo "----- install snowmelt ($(date -u)) -----"
helm install snowmelt ./charts/snowmelt -f "$UTIL/deploy/contabo-values.yaml" || { echo "INSTALL snowmelt FAILED"; exit 21; }
echo "----- install snowmelt-extras ($(date -u)) -----"
helm install snowmelt-extras "$UTIL/charts/snowmelt-extras" -f "$UTIL/deploy/contabo-extras-values.yaml" || { echo "INSTALL extras FAILED"; exit 22; }

echo "===== DEPLOY COMPLETE $(date -u) ====="
kubectl get pods -o wide
echo "===== END $(date -u) ====="
