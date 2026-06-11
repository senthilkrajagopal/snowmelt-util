#!/bin/bash
# fio-sync-sweep.sh — measure AGGREGATE synchronous-write IOPS at increasing
# parallelism, to learn whether the disk pipelines concurrent fsyncs.
#
# 4 KiB random writes, O_SYNC durability per write (ioengine=sync). Each
# `numjobs` = that many parallel sync writers (each queue-depth-1). If aggregate
# IOPS climbs with numjobs, the disk pipelines parallel syncs (→ more partitions
# can help); if it plateaus near the single-writer number, it doesn't (→ fewer,
# batched syncs win). numjobs=1 should ~match the QD1 `dd` baseline.
#
# Usage:  fio-sync-sweep.sh [dir] [seconds_per_point]   (defaults: /root/fio-test 10)
# Output -> stdout AND /root/fio-result.txt (each point appended immediately, so
# results survive a dropped SSH session).
set +e
DIR="${1:-/root/fio-test}"
RT="${2:-10}"
LOG=/root/fio-result.txt
mkdir -p "$DIR"
: > "$LOG"
log(){ echo "$@" | tee -a "$LOG"; }
log "===== fio sync-write sweep $(date -u +%FT%TZ) ====="
log "(4 KiB random writes, O_SYNC per write, ioengine=sync, ${RT}s/point, dir=$DIR)"
log ""
for NJ in 1 4 16 32 64; do
  R=$(fio --name=sw --directory="$DIR" --rw=randwrite --bs=4k --sync=1 \
          --ioengine=sync --numjobs="$NJ" --size=32M --runtime="$RT" --time_based \
          --group_reporting 2>/dev/null | grep -E "write: IOPS" | head -1)
  log "numjobs=$NJ  ->  ${R:-(no result)}"
done
rm -rf "$DIR"
log "===== done ====="
