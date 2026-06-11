#!/usr/bin/env bash
# 10-tick monitor for the streaming-datagen churn test.
# Streaming datagen: 10000-slot pool, no growth, +10% churn every 60s.
# Distinct fingerprints in metadata grow by ~1000 every churn period.
#
# Expected progression (modulo ~60-90s pipeline lag end-to-end):
#   t=0-60s     : 10001 (10000 pool + 1 predetermined)
#   t=60s+lag   : 11001 (after 1st churn settles)
#   t=120s+lag  : 12001
#   t=180s+lag  : 13001
#   t=240s+lag  : 14001
#   t=300s+lag  : 15001
#
# Per tick prints: expected_total, actual_total (per pod queries
# summed: pod-0 owns p0+p2, pod-1 owns p1+p3), md_wal_writes,
# md_table_writes.

CLI=./target/release/snowmelt-cli
POD0_FLIGHT=18815
POD1_FLIGHT=28815
MAX_TICKS=${MAX_TICKS:-10}
INTERVAL=${INTERVAL:-30}
CHURN_PERIOD=${CHURN_PERIOD:-60}
CHURN_COUNT=${CHURN_COUNT:-1000}
BASE_FP=10001
START_TS=$(date +%s)

print() { echo "[$(date +%H:%M:%S)] $*"; }

extract() {
  local dump=$1 metric=$2 part=$3 extra=${4:-}
  awk -v m="$metric" -v p="partition=\"$part\"" -v x="$extra" '
    $1 ~ "^" m "\\{" {
      labels=$1
      if (index(labels, p) == 0) next
      if (x != "" && index(labels, x) == 0) next
      print $2
      exit
    }
  ' "$dump" 2>/dev/null
}

# Per-partition distinct fp via Flight SQL. Route to the owning pod's
# per-pod port-forward so the `WHERE partition=N` predicate hits the
# correct catalog. (Cross-pod redirects use in-cluster DNS, which
# doesn't resolve from the host.)
query_distinct_for_partition() {
  local part=$1
  local endpoint=http://localhost:${POD0_FLIGHT}
  case $part in 1|3) endpoint=http://localhost:${POD1_FLIGHT} ;; esac
  $CLI --endpoint "$endpoint" \
    -c "SELECT count(DISTINCT fingerprint) FROM metric WHERE partition=${part};" 2>/dev/null \
    | awk '/^\| *[0-9]/{gsub(/[| ]/, ""); print; exit}'
}

print "monitor starting; MAX_TICKS=${MAX_TICKS}; INTERVAL=${INTERVAL}s"
print "churn config: ${CHURN_COUNT} fingerprints every ${CHURN_PERIOD}s; baseline=${BASE_FP}"

tick=0
while [[ $tick -lt $MAX_TICKS ]]; do
  tick=$((tick+1))
  d0=$(mktemp); d1=$(mktemp)
  curl -fsS --max-time 5 http://localhost:19100/metrics > "$d0" 2>/dev/null \
    || { print "tick=$tick WARN: pod-0 scrape failed"; rm -f "$d0" "$d1"; sleep "$INTERVAL"; continue; }
  curl -fsS --max-time 5 http://localhost:29100/metrics > "$d1" 2>/dev/null \
    || { print "tick=$tick WARN: pod-1 scrape failed"; rm -f "$d0" "$d1"; sleep "$INTERVAL"; continue; }

  md_wal=0; md_tbl=0
  for p in 0 1 2 3; do
    for pod in p0 p1; do
      dump=$d0; [[ "$pod" == "p1" ]] && dump=$d1
      w=$(extract "$dump" snowmelt_ingest_wal_write_count "$p" 'type="metadata"'); w=${w:-0}; w=${w%.*}
      r=$(extract "$dump" snowmelt_flush_metadata_rows_written "$p"); r=${r:-0}; r=${r%.*}
      md_wal=$((md_wal + w))
      md_tbl=$((md_tbl + r))
    done
  done

  # Per-partition distinct fp counts (Flight SQL).
  per_part=""
  total=0
  for p in 0 1 2 3; do
    cnt=$(query_distinct_for_partition "$p")
    cnt=${cnt:-0}
    per_part+="p${p}=${cnt} "
    total=$((total + cnt))
  done

  # Expected: BASE_FP + floor(uptime / CHURN_PERIOD) * CHURN_COUNT
  uptime=$(($(date +%s) - START_TS))
  cycles=$((uptime / CHURN_PERIOD))
  expected=$((BASE_FP + cycles * CHURN_COUNT))

  print "tick=$tick/${MAX_TICKS} uptime=${uptime}s churn_cycles=${cycles}"
  print "tick=$tick expected_total=${expected}  actual_total=${total}  per_partition: ${per_part}"
  print "tick=$tick md_wal_writes=${md_wal}  md_table_writes=${md_tbl}"

  rm -f "$d0" "$d1"
  if [[ $tick -lt $MAX_TICKS ]]; then sleep "$INTERVAL"; fi
done

print "monitor finished after ${MAX_TICKS} ticks"
