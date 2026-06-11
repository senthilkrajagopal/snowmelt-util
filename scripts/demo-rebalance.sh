#!/usr/bin/env bash
#
# Demo runbook for the partition transfer protocol.
#
# What it shows:
#   1. A 3-pod Snowmelt cluster comes up; partitions distribute via HRW.
#   2. Datapoints get ingested for one chosen series.
#   3. A final compaction tick rolls raw `dp/` → `dp_5m/` (the rolled-up
#      tier is in S3; raw is local-only on the original owner).
#   4. Scaling the StatefulSet from 3 → 2 replicas triggers a real
#      rebalance: the lost pod's partitions move to surviving peers,
#      `metadata.duckdb` + `snowlake.duckdb` ship through MinIO, the
#      routing layer flips, and a query against the new owner returns
#      the same data the original owner saw.
#
# Requirements:
#   - Docker Desktop k8s context active (or any cluster where you have
#     admin rights on the `default` namespace).
#   - `kubectl`, `helm`, `nc`, and the locally-built `snowmelt-cli`
#     binary (run `cargo build --release -p snowmelt-cli` first).
#   - The Snowmelt docker image already present locally (Helm chart
#     defaults to `pullPolicy: Never`):
#       docker build -f app/Dockerfile -t snowmelt:latest .
#
# Re-run friendly: every step is idempotent. If something fails
# mid-run, fix it and re-run from the top.

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-snowmelt-demo}"
NAMESPACE="${NAMESPACE:-default}"
RELEASE="${RELEASE:-snowmelt}"
CHART="$(cd "$(dirname "$0")/.." && pwd)/charts/snowmelt"
CLI="${CLI:-$(cd "$(dirname "$0")/.." && pwd)/target/release/snowmelt-cli}"
TARGET_PARTITION="${TARGET_PARTITION:-3}"

if [[ ! -x "$CLI" ]]; then
  echo "ERR: snowmelt-cli not built. Run:"
  echo "  cargo build --release -p snowmelt-cli"
  exit 1
fi

step() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
info() { printf "  \033[2m%s\033[0m\n" "$*"; }

step "1. Install Snowmelt with 3 replicas"
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --set replicaCount=3 \
  --wait --timeout 5m

step "2. Wait for all 3 pods to become Ready"
kubectl -n "$NAMESPACE" rollout status statefulset/"$RELEASE" --timeout=5m

step "3. Port-forward snowmelt-0:8815 for snowmelt-cli"
kubectl -n "$NAMESPACE" port-forward pod/"$RELEASE"-0 8815:8815 >/tmp/snowmelt-demo-pf.log 2>&1 &
PF_PID=$!
trap "kill $PF_PID 2>/dev/null || true" EXIT
sleep 2
info "port-forward pid $PF_PID; logs at /tmp/snowmelt-demo-pf.log"

step "4. Initial routing — every partition reports its owner"
"$CLI" --endpoint http://localhost:8815 --show-routing | head -20
INITIAL_OWNER=$(
  "$CLI" --endpoint http://localhost:8815 -c \
    "SELECT node_assignment FROM partitions WHERE partition = $TARGET_PARTITION" \
    | tail -n +2 | head -1 || true
)
info "partition $TARGET_PARTITION current owner: $INITIAL_OWNER"

step "5. Ingest datapoints via the OTLP collector (one chosen series)"
# Snowmelt's built-in datagen task seeds traffic by default. If your
# deployment disables datagen, replace this step with a real OTLP push
# against snowmelt-0:4317.
info "datagen runs on each pod automatically; wait 30s for series to land"
sleep 30

step "6. Pre-rebalance: count datapoints for the target partition"
PRE_COUNT=$(
  "$CLI" --endpoint http://localhost:8815 -c \
    "SELECT count(*) FROM metric WHERE partition = $TARGET_PARTITION" \
    | tail -n +2 | head -1 || echo "0"
)
info "rows in partition $TARGET_PARTITION: $PRE_COUNT"

step "7. Scale down to 2 replicas — triggers rebalance for pod 2's partitions"
kubectl -n "$NAMESPACE" scale statefulset/"$RELEASE" --replicas=2
kubectl -n "$NAMESPACE" rollout status statefulset/"$RELEASE" --timeout=5m

step "8. Watch the rebalance logs"
info "following snowmelt-0's logs for 30s — look for 'rebalance: ...' lines"
kubectl -n "$NAMESPACE" logs "$RELEASE"-0 --since=1m --tail=200 \
  | grep -E "rebalance:|cluster:|cluster_event:" | head -40 || true

step "9. Post-rebalance: routing should show the new owner"
sleep 5
"$CLI" --endpoint http://localhost:8815 --show-routing | head -20

step "10. Post-rebalance: query the same partition again"
POST_COUNT=$(
  "$CLI" --endpoint http://localhost:8815 -c \
    "SELECT count(*) FROM metric WHERE partition = $TARGET_PARTITION" \
    | tail -n +2 | head -1 || echo "0"
)
info "rows in partition $TARGET_PARTITION (post-transfer): $POST_COUNT"

if [[ "$PRE_COUNT" == "$POST_COUNT" && "$PRE_COUNT" != "0" ]]; then
  printf "\n\033[1;32m✓ Rebalance demo succeeded — same row count, routing followed.\033[0m\n"
else
  printf "\n\033[1;33m⚠ Row counts differ: pre=%s post=%s\033[0m\n" "$PRE_COUNT" "$POST_COUNT"
  printf "  Inspect logs above. Common causes:\n"
  printf "    - raw 'dp/' files were not rolled up to S3 before the transfer\n"
  printf "      (Phase 2 ships only metadata + snowlake DuckDBs)\n"
  printf "    - the datagen task is disabled in your values.yaml\n"
fi

step "Cleanup"
info "leaving the StatefulSet at 2 replicas. To uninstall:"
info "  helm uninstall $RELEASE -n $NAMESPACE"
