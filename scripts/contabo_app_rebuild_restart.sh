#!/usr/bin/env bash
# Rebuild ONLY the snowmelt-app image (engine shard-pushdown change), import to
# k3s containerd, rollout-restart the StatefulSet to pick it up. PRESERVES PVCs
# (no data wipe) so EXPLAIN ANALYZE can re-run on the same shape_sine/node data.
set -uo pipefail
cd /root/snowmelt || exit 1
LOG=/root/deploy.log; : > "$LOG"; exec >"$LOG" 2>&1
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo "===== APP REBUILD START $(date -u) ====="

echo "----- build snowmelt-app (context=., cargo-chef deps cached) -----"
docker build -f app/Dockerfile -t snowmelt-app:latest . || { echo "BUILD FAILED"; exit 11; }
echo "----- import to k3s containerd -----"
docker save snowmelt-app:latest | k3s ctr -n k8s.io images import - || { echo "IMPORT FAILED"; exit 12; }

echo "----- rollout restart snowmelt STS (picks up new :latest, keeps PVCs) -----"
kubectl rollout restart statefulset/snowmelt
kubectl rollout status statefulset/snowmelt --timeout=360s || echo "(STS rollout slow; continuing)"

echo "----- restart datagen so it re-resolves the new snowmelt pod IPs (backends) -----"
kubectl rollout restart deploy/snowmelt-extras-datagen

echo "===== APP REBUILD COMPLETE $(date -u) ====="
kubectl get pods -o wide | grep -E 'snowmelt-[0-9]|datagen'
echo "===== END $(date -u) ====="
