#!/usr/bin/env python3
"""
Manual side-by-side check for the Phase β scalar functions
(math, trig, calendar, composition).

Runs ~20 representative exprs as instant + range queries against
snowmelt and Prometheus, prints a single-screen report. Doesn't
exhaustively cover all 37 fns — see the per-module unit tests in
`engine/src/promql_udf/scalar_fns.rs` for that. This script is for
eyeballing live behaviour after a deploy.

Usage (port-forwards already up):

    python3 scripts/check_scalar_fns.py
    python3 scripts/check_scalar_fns.py --metric jvm_memory_used

Stdlib-only.
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


# Same OpenMetrics-suffix map as the other check_*.py scripts.
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


# (display_name, expr_template). `{m}` filled with --metric, `{r}`
# with --range. Composition cases use specific metrics inline since
# rate/over_time semantics differ per metric kind.
EXPRS: list[tuple[str, str]] = [
    # Math (no composition)
    ("abs",           "abs({m})"),
    ("ceil",          "ceil({m})"),
    ("floor",         "floor({m})"),
    ("round(v, 100)", "round({m}, 100)"),
    ("clamp(v, 0, 1e9)", "clamp({m}, 0, 1000000000)"),
    ("clamp_min(v, 1)",  "clamp_min({m}, 1)"),
    ("clamp_max(v, 1e9)","clamp_max({m}, 1000000000)"),
    ("exp(ln(v))",    "exp(ln({m}))"),
    ("ln",            "ln({m})"),
    ("log2",          "log2({m})"),
    ("log10",         "log10({m})"),
    ("sqrt",          "sqrt({m})"),
    ("sgn",           "sgn({m})"),
    # Trig
    ("sin",           "sin({m})"),
    ("cos",           "cos({m})"),
    ("atan",          "atan({m})"),
    ("deg(rad(v))",   "deg(rad({m}))"),
    # Calendar (use the timestamp of each sample, not its value)
    ("hour",          "hour({m})"),
    ("day_of_month",  "day_of_month({m})"),
    ("year",          "year({m})"),
    ("timestamp",     "timestamp({m})"),
    # Composition with rate / over_time / aggregates
    ("abs(rate(jvm_class_loaded[1m]))", "abs(rate(jvm_class_loaded[1m]))"),
    ("floor(avg_over_time(jvm_thread_count[5m]))", "floor(avg_over_time(jvm_thread_count[5m]))"),
    ("sgn(deriv(jvm_memory_used[5m]))", "sgn(deriv(jvm_memory_used[5m]))"),
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


def first_value(resp: dict[str, Any]) -> str:
    if resp.get("status") != "success":
        return f"ERR: {resp.get('error', '?')[:50]}"
    rs = resp.get("data", {}).get("result", [])
    if not rs:
        return "<empty>"
    val = rs[0].get("value", [None, "?"])[1]
    try:
        return f"{float(val):.4g}"
    except (TypeError, ValueError):
        return str(val)


def to_prom_expr(snow_expr: str) -> str:
    """Translate a snowmelt-side PromQL expr to its prom-side
    counterpart by substituting any known snow metric name with the
    prom suffixed variant. Cheap lexical replace — fine because our
    metric names are unique tokens. Avoids substituting a name that
    already has the suffix (`jvm_memory_used_bytes` stays as-is)."""
    out = snow_expr
    for snow, prom in PROM_SUFFIX_MAP.items():
        if snow == prom:
            continue
        # Substitute occurrences of the snow name that aren't already
        # suffixed. Simple guard: surround the substitution to avoid
        # double-suffixing if someone passes a prom name on the cli.
        if prom in out:
            continue
        out = out.replace(snow, prom)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--metric", default="jvm_memory_used",
                    help="snowmelt metric for math/trig/calendar fns (default: jvm_memory_used)")
    ap.add_argument("--prom-metric", default=None,
                    help="prom metric for math/trig/calendar fns (default: auto-derive via OpenMetrics suffix map)")
    ap.add_argument("--range", default="5m",
                    help="range duration for compositional exprs (default: 5m)")
    args = ap.parse_args()

    snow_metric = args.metric
    prom_metric = args.prom_metric or to_prom_name(snow_metric)

    end = int(os.environ.get("SNOWMELT_PROBE_END", int(time.time()) - 30))
    print(f"snow metric: {snow_metric}    prom metric: {prom_metric}    range: [{args.range}]    eval: {end}")
    print(f"snowmelt: {SNOW}    prometheus: {PROM}")
    print()

    print(f"{'expression':<48} {'snow':>14} {'prom':>14}  match?")
    print("-" * 96)
    for name, tpl in EXPRS:
        snow_expr = tpl.format(m=snow_metric, r=args.range)
        # Prom side: substitute the metric arg AND translate any
        # other snow metric names that show up in compositional
        # exprs (like `jvm_class_loaded` inside `abs(rate(...))`).
        prom_expr = to_prom_expr(tpl.format(m=prom_metric, r=args.range))
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
