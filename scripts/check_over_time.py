#!/usr/bin/env python3
"""
Manual side-by-side check for the Phase α `*_over_time` family.

Runs each function as both an INSTANT query (`/api/v1/query`) and a
RANGE query (`/api/v1/query_range`) against:

  * snowmelt PromQL HTTP API at http://localhost:9092/promql/
  * upstream Prometheus at        http://localhost:9090/

Prints a formatted side-by-side report — counts, sample timestamps,
and either equality or per-step % diff. Useful for ad-hoc poking
when the full `compare_dashboards.py` harness verdict is unhelpful
(e.g. a fn returns sane values but timestamps drift, you want to see
what's there).

Usage (port-forwards already up — see
`.claude/memory/feedback_compare_prom_snowmelt.md`):

    python3 scripts/check_over_time.py
    python3 scripts/check_over_time.py --metric jvm_thread_count
    python3 scripts/check_over_time.py --range 10m --step 30s

Stdlib-only — no extra deps.
"""
import argparse
import json
import sys
import os
import time
import urllib.parse
import urllib.request
from typing import Any

SNOW = "http://localhost:9092/promql/api/v1"
PROM = "http://localhost:9090/api/v1"

# Prom's OTLP receiver appends OpenMetrics-style unit suffixes to
# byte / ratio / second gauges and `_total` to counters. The snow
# names mirror the original OTLP attributes verbatim. This map is
# best-effort — pass `--prom-metric` to override on the cli.
PROM_SUFFIX_MAP = {
    "jvm_memory_used":              "jvm_memory_used_bytes",
    "jvm_memory_committed":         "jvm_memory_committed_bytes",
    "jvm_class_loaded":             "jvm_class_loaded_total",
    "jvm_cpu_recent_utilization":   "jvm_cpu_recent_utilization_ratio",
    "process_memory_usage":         "process_memory_usage_bytes",
    "process_cpu_utilization":      "process_cpu_utilization_ratio",
    "redis_memory_used":            "redis_memory_used_bytes",
    "mysql_query_count":            "mysql_query_count_total",
    "redis_commands_processed":     "redis_commands_processed_total",
    "postgresql_commits":           "postgresql_commits_total",
    "mongodb_operation_count":      "mongodb_operation_count_total",
}


def to_prom_name(snow_name: str) -> str:
    """Best-effort translation of a snowmelt metric name to its
    Prom-side counterpart. Falls back to the same name when no
    suffix is applicable (e.g. `jvm_thread_count`)."""
    return PROM_SUFFIX_MAP.get(snow_name, snow_name)


# (display_name, expr_template) — `{m}` is filled with the chosen metric,
# `{r}` with the range duration string ("5m", "10m", …).
OVER_TIME_FNS: list[tuple[str, str]] = [
    ("avg_over_time",     "avg_over_time({m}[{r}])"),
    ("min_over_time",     "min_over_time({m}[{r}])"),
    ("max_over_time",     "max_over_time({m}[{r}])"),
    ("sum_over_time",     "sum_over_time({m}[{r}])"),
    ("count_over_time",   "count_over_time({m}[{r}])"),
    ("last_over_time",    "last_over_time({m}[{r}])"),
    ("first_over_time",   "first_over_time({m}[{r}])"),
    ("present_over_time", "present_over_time({m}[{r}])"),
    ("stddev_over_time",  "stddev_over_time({m}[{r}])"),
    ("stdvar_over_time",  "stdvar_over_time({m}[{r}])"),
    ("mad_over_time",     "mad_over_time({m}[{r}])"),
]


def parse_duration(s: str) -> int:
    """e.g. '5m' -> 300, '30s' -> 30, '1h' -> 3600."""
    units = {"s": 1, "m": 60, "h": 3600}
    if s and s[-1] in units:
        return int(s[:-1]) * units[s[-1]]
    return int(s)


def http_get(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "error", "error": str(e)}


def instant_query(base: str, expr: str, ts: int) -> dict[str, Any]:
    q = urllib.parse.urlencode({"query": expr, "time": ts})
    return http_get(f"{base}/query?{q}")


def range_query(base: str, expr: str, start: int, end: int, step: int) -> dict[str, Any]:
    q = urllib.parse.urlencode(
        {"query": expr, "start": start, "end": end, "step": step}
    )
    return http_get(f"{base}/query_range?{q}")


def first_value(resp: dict[str, Any]) -> str:
    """Pull a single representative numeric value from an instant
    query response, or an error/empty marker."""
    if resp.get("status") != "success":
        return f"ERR: {resp.get('error', '?')[:80]}"
    rs = resp.get("data", {}).get("result", [])
    if not rs:
        return "<empty>"
    val = rs[0].get("value", [None, "?"])[1]
    try:
        return f"{float(val):.4g}"
    except (TypeError, ValueError):
        return str(val)


def range_summary(resp: dict[str, Any]) -> tuple[int, int, str]:
    """Return (series, points-on-first-series, sample value at first ts)."""
    if resp.get("status") != "success":
        return (0, 0, f"ERR: {resp.get('error', '?')[:60]}")
    rs = resp.get("data", {}).get("result", [])
    if not rs:
        return (0, 0, "<empty>")
    pts = rs[0].get("values", [])
    sample = "<no points>"
    if pts:
        try:
            sample = f"{float(pts[0][1]):.4g}"
        except (TypeError, ValueError):
            sample = str(pts[0][1])
    return (len(rs), len(pts), sample)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metric", default="jvm_memory_used",
                    help="snowmelt metric name to feed each over_time fn (default: jvm_memory_used)")
    ap.add_argument("--prom-metric", default=None,
                    help="prom metric name (default: auto-derive via OpenMetrics suffix map)")
    ap.add_argument("--range", default="5m",
                    help="range duration for each `[range]` (default: 5m)")
    ap.add_argument("--step", default="30s",
                    help="range-query step (default: 30s)")
    ap.add_argument("--window", default="5m",
                    help="how far back the range query covers (default: 5m)")
    args = ap.parse_args()

    snow_metric = args.metric
    prom_metric = args.prom_metric or to_prom_name(snow_metric)
    rng = args.range
    step = parse_duration(args.step)
    window_secs = parse_duration(args.window)
    end = int(os.environ.get("SNOWMELT_PROBE_END", int(time.time()) - 30))  # 30s lag for ingest
    start = end - window_secs

    print(f"snow metric: {snow_metric}    prom metric: {prom_metric}")
    print(f"range arg: [{rng}]    step: {step}s    window: {window_secs}s")
    print(f"snowmelt: {SNOW}    prometheus: {PROM}")
    print()

    # --- Instant queries: one row per fn, snow vs prom values ---
    print("=== instant queries (single value at end-30s) ===")
    print(f"{'function':<22} {'snow':>14} {'prom':>14}  match?")
    print("-" * 70)
    for name, tpl in OVER_TIME_FNS:
        snow_expr = tpl.format(m=snow_metric, r=rng)
        prom_expr = tpl.format(m=prom_metric, r=rng)
        sn = first_value(instant_query(SNOW, snow_expr, end))
        pr = first_value(instant_query(PROM, prom_expr, end))
        match = ""
        try:
            if abs(float(sn) - float(pr)) / max(abs(float(pr)), 1e-9) < 0.05:
                match = "OK"
            else:
                match = "DIFF"
        except (TypeError, ValueError):
            match = "?"
        print(f"{name:<22} {sn:>14} {pr:>14}  {match}")

    # --- Range queries: series counts + first-point value ---
    print()
    print(f"=== range queries (start={start} end={end} step={step}s) ===")
    print(f"{'function':<22} {'snow ser/pts/v0':>22} {'prom ser/pts/v0':>22}")
    print("-" * 70)
    for name, tpl in OVER_TIME_FNS:
        snow_expr = tpl.format(m=snow_metric, r=rng)
        prom_expr = tpl.format(m=prom_metric, r=rng)
        sn_n, sn_p, sn_v = range_summary(range_query(SNOW, snow_expr, start, end, step))
        pr_n, pr_p, pr_v = range_summary(range_query(PROM, prom_expr, start, end, step))
        print(f"{name:<22} {f'{sn_n}/{sn_p}/{sn_v}':>22} {f'{pr_n}/{pr_p}/{pr_v}':>22}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
