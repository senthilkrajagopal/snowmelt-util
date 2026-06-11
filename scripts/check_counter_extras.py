#!/usr/bin/env python3
"""
Manual side-by-side check for the Phase γ counter-aware extras.

Runs each of the 5 functions (`delta`, `idelta`, `deriv`, `changes`,
`resets`) as both an INSTANT query (`/api/v1/query`) and a RANGE
query (`/api/v1/query_range`) against:

  * snowmelt PromQL HTTP API at http://localhost:9092/promql/
  * upstream Prometheus at        http://localhost:9090/

Prints a single-screen side-by-side report — useful when
`compare_dashboards.py` flags a verdict and you want to eyeball the
actual values for just these fns. `delta`/`idelta`/`deriv` go on a
gauge (default `jvm_memory_used`); `changes`/`resets` go on a
counter (default `jvm_thread_count` for changes, `jvm_class_loaded`
for resets — `jvm_class_loaded_total` on the prom side because
prom's OTLP receiver auto-suffixes counters).

Usage (port-forwards already up):

    python3 scripts/check_counter_extras.py
    python3 scripts/check_counter_extras.py --gauge jvm_thread_count --range 10m
    python3 scripts/check_counter_extras.py --window 30m --step 30s

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

# Same OpenMetrics-suffix map as `check_over_time.py`. Used to
# auto-derive the prom-side metric name from the snowmelt-side
# default unless the user passes `--prom-gauge` / `--prom-counter`.
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
    return PROM_SUFFIX_MAP.get(snow_name, snow_name)


# Each row: (display_name, snow_expr_template, prom_expr_template).
# Caller supplies separate gauge / counter names for snow and prom.
def build_panels(
    snow_gauge: str, prom_gauge: str,
    snow_counter: str, prom_counter: str, rng: str,
) -> list[tuple[str, str, str]]:
    return [
        (f"delta({snow_gauge}[{rng}])",
         f"delta({snow_gauge}[{rng}])",
         f"delta({prom_gauge}[{rng}])"),
        (f"idelta({snow_gauge}[{rng}])",
         f"idelta({snow_gauge}[{rng}])",
         f"idelta({prom_gauge}[{rng}])"),
        (f"deriv({snow_gauge}[{rng}])",
         f"deriv({snow_gauge}[{rng}])",
         f"deriv({prom_gauge}[{rng}])"),
        (f"changes({snow_counter}[{rng}])",
         f"changes({snow_counter}[{rng}])",
         f"changes({prom_counter}[{rng}])"),
        (f"resets({snow_counter}[{rng}])",
         f"resets({snow_counter}[{rng}])",
         f"resets({prom_counter}[{rng}])"),
    ]


def parse_duration(s: str) -> int:
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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--gauge", default="jvm_memory_used",
                    help="snowmelt gauge metric for delta/idelta/deriv (default: jvm_memory_used)")
    ap.add_argument("--counter", default="jvm_class_loaded",
                    help="snowmelt counter metric for changes/resets (default: jvm_class_loaded)")
    ap.add_argument("--prom-gauge", default=None,
                    help="prom gauge metric (default: auto-derive via OpenMetrics suffix map)")
    ap.add_argument("--prom-counter", default=None,
                    help="prom counter metric (default: auto-derive via OpenMetrics suffix map)")
    ap.add_argument("--range", default="5m",
                    help="range duration for each `[range]` (default: 5m)")
    ap.add_argument("--step", default="30s",
                    help="range-query step (default: 30s)")
    ap.add_argument("--window", default="5m",
                    help="how far back the range query covers (default: 5m)")
    args = ap.parse_args()

    rng = args.range
    step = parse_duration(args.step)
    window_secs = parse_duration(args.window)
    end = int(os.environ.get("SNOWMELT_PROBE_END", int(time.time()) - 30))
    start = end - window_secs

    prom_gauge = args.prom_gauge or to_prom_name(args.gauge)
    prom_counter = args.prom_counter or to_prom_name(args.counter)
    panels = build_panels(args.gauge, prom_gauge, args.counter, prom_counter, rng)

    print(f"snow gauge: {args.gauge}    prom gauge: {prom_gauge}")
    print(f"snow counter: {args.counter}    prom counter: {prom_counter}")
    print(f"range: [{rng}]    step: {step}s    window: {window_secs}s")
    print(f"snowmelt: {SNOW}    prometheus: {PROM}")
    print()

    print("=== instant queries (single value at end-30s) ===")
    print(f"{'function':<48} {'snow':>14} {'prom':>14}  match?")
    print("-" * 96)
    for name, snow_expr, prom_expr in panels:
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
        print(f"{name:<48} {sn:>14} {pr:>14}  {match}")

    print()
    print(f"=== range queries (start={start} end={end} step={step}s) ===")
    print(f"{'function':<48} {'snow ser/pts/v0':>22} {'prom ser/pts/v0':>22}")
    print("-" * 96)
    for name, snow_expr, prom_expr in panels:
        sn_n, sn_p, sn_v = range_summary(range_query(SNOW, snow_expr, start, end, step))
        pr_n, pr_p, pr_v = range_summary(range_query(PROM, prom_expr, start, end, step))
        print(f"{name:<48} {f'{sn_n}/{sn_p}/{sn_v}':>22} {f'{pr_n}/{pr_p}/{pr_v}':>22}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
