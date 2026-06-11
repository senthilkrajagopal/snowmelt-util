#!/usr/bin/env bash
# disk-bench.sh — quick disk read/write speed test (dd-based, no extra deps).
#
# Usage:  disk-bench.sh [target_dir] [size_MB]      # defaults: /root 256
#
# Measures the filesystem that holds <target_dir>. Built to quantify the
# Contabo single disk (sda4) that bottlenecks snowmelt ingest. Reports:
#   1. sequential WRITE  (buffered + fdatasync) — realistic bulk-write MB/s
#   2. sequential WRITE  (O_DIRECT)             — true device write, no page cache
#   3. sequential READ   (cold cache)           — bulk-read MB/s
#   4. 4 KiB SYNC writes (O_DSYNC)              — WAL-fsync-like; IOPS-bound rate
#      (this is the number that actually limits snowmelt's WAL on a slow disk)
#
# Output goes to stdout AND <target_dir>/disk-bench.txt, so results survive a
# dropped SSH session. Cleans up its temp file. Run as a user that can write
# <target_dir> (drop_caches for the cold read needs root; skipped otherwise).
set +e
DIR="${1:-/root}"
SIZE_MB="${2:-256}"
F="$DIR/.diskbench.tmp"
LOG="$DIR/disk-bench.txt"
exec > >(tee "$LOG") 2>&1

echo "===== disk-bench $(date -u +%FT%TZ)  target=$DIR  size=${SIZE_MB}MB ====="

echo "[1/4] sequential WRITE (buffered, fdatasync at end):"
dd if=/dev/zero of="$F" bs=1M count="$SIZE_MB" conv=fdatasync 2>&1 | tail -1

echo "[2/4] sequential WRITE (O_DIRECT, bypasses page cache):"
dd if=/dev/zero of="$F" bs=1M count="$SIZE_MB" oflag=direct 2>&1 | tail -1

echo "[3/4] sequential READ (cold cache):"
sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || echo "  (could not drop caches — read may be cached)"
dd if="$F" of=/dev/null bs=1M 2>&1 | tail -1

echo "[4/4] 4KiB SYNCHRONOUS writes x10000 (O_DSYNC; WAL-fsync-like, IOPS-bound):"
dd if=/dev/zero of="$F" bs=4k count=10000 oflag=dsync 2>&1 | tail -1

rm -f "$F"
echo "===== done ====="
