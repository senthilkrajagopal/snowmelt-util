#!/usr/bin/env bash
# CLEAN STRESS REDEPLOY on Contabo: wipe ALL PVCs (data loss), reinstall in stress
# mode (quickwit/logs/otel-demo OFF), datagen = 2 pods × 25k = 50k dp/s.
# Run detached: systemd-run --unit=clean-redeploy --collect bash deploy-stress-clean.sh
set -uo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
SM=/root/snowmelt/charts/snowmelt
EX=/root/snowmelt-util/charts/snowmelt-extras
DEP=/root/snowmelt-util/deploy
LOG=/root/clean-stress-redeploy.log
: > "$LOG"; exec >"$LOG" 2>&1
echo "===== CLEAN STRESS REDEPLOY (WIPE PVCs) $(date -u) ====="

echo "----- uninstall all releases -----"
helm uninstall snowmelt-extras -n default 2>&1 | tail -1 || true
helm uninstall snowmelt-demo   -n demo    2>&1 | tail -1 || true
helm uninstall snowmelt        -n default 2>&1 | tail -1 || true

echo "----- wait for pods to terminate -----"
kubectl wait --for=delete pod --all -n default --timeout=180s 2>&1 | tail -2 || true
kubectl wait --for=delete pod --all -n demo    --timeout=60s  2>&1 | tail -1 || true

echo "----- DELETE ALL PVCs (data wipe) -----"
kubectl delete pvc --all -n default --timeout=150s 2>&1 | tail -12 || true
kubectl delete pvc --all -n demo    --timeout=60s  2>&1 | tail -3  || true
echo "remaining PVCs: $(kubectl get pvc -A --no-headers 2>/dev/null | grep -vc NAME)"

echo "----- install snowmelt (STRESS: quickwit + log-collector OFF) -----"
helm install snowmelt "$SM" -f "$DEP/contabo-values.yaml" \
  --set quickwit.enabled=false --set otelLogsCollector.enabled=false \
  -n default --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error|cannot|failed"

echo "----- install datagen (STRESS: 2 pods × 25k) -----"
helm install snowmelt-extras "$EX" -f "$DEP/contabo-extras-stress.yaml" \
  -n default --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error|cannot|failed"

echo "===== INSTALLED $(date -u); polling readiness ====="
for i in $(seq 1 40); do
  nr=$(kubectl get pods -n default --no-headers 2>/dev/null | grep -vcE "Running|Completed")
  echo "[poll $i] not-ready=$nr"
  [ "$nr" = "0" ] && { echo "ALL READY"; break; }
  sleep 15
done
echo "===== FINAL $(date -u) ====="
kubectl get pods -n default 2>&1 | grep -E "snowmelt-[0-9]|datagen|collector|minio|nats|postgres|clickhouse"
echo "--- datagen config ---"
kubectl get cm snowmelt-extras-datagen -n default -o jsonpath="{.data.config\.yaml}" 2>/dev/null | grep -iE "streaming-pool-size|otlp-batch|enabled: true" | head -3
echo "--- collector headless? ---"
kubectl get svc snowmelt-otel-collector -n default -o jsonpath="clusterIP={.spec.clusterIP}{\"\n\"}" 2>/dev/null
echo "===== END $(date -u) ====="
