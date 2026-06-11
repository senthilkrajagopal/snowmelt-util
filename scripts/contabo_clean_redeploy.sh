#!/usr/bin/env bash
# Contabo CLEAN redeploy: build 3 images on the box, import to k3s containerd,
# uninstall BOTH releases, delete ALL PVCs, reinstall BOTH charts.
# Run detached via systemd-run; tail /root/deploy.log for progress.
set -euo pipefail
cd /root/snowmelt || exit 1
UTIL="${SNOWMELT_UTIL:-/root/snowmelt-util}"   # this repo, checked out beside snowmelt
LOG=/root/deploy.log
: > "$LOG"
exec >"$LOG" 2>&1
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo "===== DEPLOY START $(date -u) ====="

build_import() {
  local name="$1" dockerfile="$2" context="$3"
  echo "----- build ${name} (context=${context}) ($(date -u)) -----"
  docker build -f "${dockerfile}" -t "${name}:latest" "${context}" || { echo "BUILD FAILED: ${name}"; exit 11; }
  echo "----- import ${name} → k3s containerd ($(date -u)) -----"
  docker save "${name}:latest" | k3s ctr -n k8s.io images import - || { echo "IMPORT FAILED: ${name}"; exit 12; }
}

# app + datagen build from the workspace ROOT (cargo workspace); the UI
# Dockerfile's COPYs are unprefixed (COPY package.json ./), so its context
# MUST be ui/ — passing . fails with "package.json: file does not exist".
build_import snowmelt-app     app/Dockerfile     .
build_import snowmelt-datagen datagen/Dockerfile .
build_import snowmelt-ui      ui/Dockerfile      ui

echo "===== IMAGES DONE; CLEAN REDEPLOY ($(date -u)) ====="
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
