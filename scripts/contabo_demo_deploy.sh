#!/usr/bin/env bash
# Deploy the OpenTelemetry Demo (snowmelt-demo chart) against the snowmelt stack:
#  - helm upgrade snowmelt (Quickwit ON, for traces+logs)
#  - helm upgrade snowmelt-extras (datagen -> 1, free I/O for the demo)
#  - helm install snowmelt-demo in the `demo` namespace (collector -> snowmelt + quickwit)
# No image builds — the demo uses public images (k3s can pull them).
set -euo pipefail
cd /root/snowmelt || exit 1
UTIL="${SNOWMELT_UTIL:-/root/snowmelt-util}"   # this repo, checked out beside snowmelt
LOG=/root/deploy.log; : > "$LOG"; exec >"$LOG" 2>&1
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
echo "===== DEMO DEPLOY START $(date -u) ====="

# Safety: make sure the otel helm repo is known (the subchart .tgz is vendored in
# charts/snowmelt-demo/charts/, but this avoids any dependency-resolution hiccup).
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null 2>&1 || true
helm repo update open-telemetry >/dev/null 2>&1 || true

echo "----- helm upgrade snowmelt (Quickwit ON) -----"
helm upgrade snowmelt ./charts/snowmelt -f "$UTIL/deploy/contabo-values.yaml" || { echo "UPGRADE snowmelt FAILED"; exit 21; }

echo "----- helm upgrade snowmelt-extras (datagen=1) -----"
helm upgrade snowmelt-extras "$UTIL/charts/snowmelt-extras" -f "$UTIL/deploy/contabo-extras-values.yaml" || { echo "UPGRADE extras FAILED"; exit 22; }

echo "----- helm install snowmelt-demo (namespace demo) -----"
helm dependency build "$UTIL/charts/snowmelt-demo" >/dev/null 2>&1 || true
helm upgrade --install snowmelt-demo "$UTIL/charts/snowmelt-demo" -n demo --create-namespace -f "$UTIL/deploy/contabo-demo-values.yaml" \
  || { echo "INSTALL snowmelt-demo FAILED"; exit 23; }

echo "----- wait for quickwit + demo pods -----"
kubectl rollout status statefulset/snowmelt-quickwit -n default --timeout=240s || echo "(quickwit slow; continuing)"
sleep 15

echo "===== DEMO DEPLOY COMPLETE $(date -u) ====="
echo "--- default ns (quickwit/datagen) ---"
kubectl get pods -n default --no-headers 2>/dev/null | grep -E 'quickwit|datagen' | awk '{print $1, $3}'
echo "--- demo ns ---"
kubectl get pods -n demo --no-headers 2>/dev/null | awk '{print $1, $3}'
echo "===== END $(date -u) ====="
