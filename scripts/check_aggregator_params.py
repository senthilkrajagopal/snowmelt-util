#!/usr/bin/env python3
"""
Manual side-by-side check for the Phase δ aggregator-parameter
extension — `topk`, `bottomk`, `quantile`, `count_values`, plus
the no-param `stddev` / `stdvar`.

Runs ~10 representative exprs as instant queries against snowmelt
+ prom; prints series count + sample value per backend with an
OK/DIFF verdict (5% relative tolerance).

For multi-output ops (`topk` / `bottomk` / `count_values`) we
compare the SUM of returned values per side instead of any single
sample — that's the simplest scalar invariant that survives the
multi-series shape.

Usage (port-forwards already up):

    python3 scripts/check_aggregator_params.py
    python3 scripts/check_aggregator_params.py --metric jvm_thread_count
    python3 scripts/check_aggregator_params.py --range 10m

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


def to_prom_expr(snow_expr: str) -> str:
    out = snow_expr
    for snow, prom in PROM_SUFFIX_MAP.items():
        if snow == prom or prom in out:
            continue
        out = out.replace(snow, prom)
    return out


# (display_name, expr_template). `{m}` is the gauge metric, `{c}`
# the counter metric, `{r}` the range duration. Templates use both
# — composition cases inline `jvm_class_loaded` / `jvm_thread_count`.
EXPRS: list[tuple[str, str]] = [
    ("topk(5, m)",                          "topk(5, {m})"),
    ("bottomk(5, m)",                       "bottomk(5, {m})"),
    ("topk(3, rate({c}[{r}]))",             "topk(3, rate({c}[{r}]))"),
    ("quantile(0.5, m)",                    "quantile(0.5, {m})"),
    ("quantile(0.95, m)",                   "quantile(0.95, {m})"),
    ("quantile by (cloud_provider)(0.99,m)","quantile by (cloud_provider) (0.99, {m})"),
    ("stddev(m)",                           "stddev({m})"),
    ("stdvar(m)",                           "stdvar({m})"),
    ("stddev by (cloud_provider)(m)",       "stddev by (cloud_provider) ({m})"),
    ("count_values(\"v\", jvm_thread_count)","count_values(\"v\", jvm_thread_count)"),
]


def http_get(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "error", "error": str(e)}


def instant_query(base: str, expr: str, ts: int) -> dict[str, Any]:
    q = urllib.parse.urlencode({"query": expr, "time": ts})
    return http_get(f"{base}/query?{q}")


def summarise(resp: dict[str, Any]) -> tuple[int, str, float | None]:
    """Return (series_count, sample_str, sum_of_values_or_None)."""
    if resp.get("status") != "success":
        return (0, f"ERR: {resp.get('error', '?')[:50]}", None)
    rs = resp.get("data", {}).get("result", [])
    if not rs:
        return (0, "<empty>", None)
    total = 0.0
    sample = ""
    for i, s in enumerate(rs):
        try:
            v = float(s["value"][1])
            total += v
            if i == 0:
                sample = f"{v:.4g}"
        except (TypeError, ValueError, KeyError):
            pass
    return (len(rs), sample, total)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--metric", default="jvm_memory_used",
                    help="snowmelt gauge metric for topk/bottomk/quantile/stddev/stdvar (default: jvm_memory_used)")
    ap.add_argument("--prom-metric", default=None,
                    help="prom gauge metric (default: auto-derive via OpenMetrics suffix map)")
    ap.add_argument("--range", default="5m",
                    help="range duration for compositional exprs (default: 5m)")
    args = ap.parse_args()

    snow_metric = args.metric
    prom_metric = args.prom_metric or to_prom_name(snow_metric)
    counter = "jvm_class_loaded"

    end = int(os.environ.get("SNOWMELT_PROBE_END", int(time.time()) - 30))
    print(f"snow metric: {snow_metric}    prom metric: {prom_metric}    range: [{args.range}]    eval: {end}")
    print(f"snowmelt: {SNOW}    prometheus: {PROM}")
    print()

    print(f"{'expression':<48} {'snow ser/sample/Σ':>26} {'prom ser/sample/Σ':>26}  match?")
    print("-" * 110)
    for name, tpl in EXPRS:
        snow_expr = tpl.format(m=snow_metric, c=counter, r=args.range)
        prom_expr = to_prom_expr(tpl.format(m=prom_metric, c=counter, r=args.range))
        sn_n, sn_s, sn_sum = summarise(instant_query(SNOW, snow_expr, end))
        pr_n, pr_s, pr_sum = summarise(instant_query(PROM, prom_expr, end))
        match = ""
        if sn_sum is not None and pr_sum is not None:
            denom = max(abs(pr_sum), 1e-9)
            if abs(sn_sum - pr_sum) / denom < 0.05:
                match = "OK"
            else:
                match = "DIFF"
        else:
            match = "?"
        sn_disp = f"{sn_n}/{sn_s}/{sn_sum:.4g}" if sn_sum is not None else f"{sn_n}/{sn_s}/-"
        pr_disp = f"{pr_n}/{pr_s}/{pr_sum:.4g}" if pr_sum is not None else f"{pr_n}/{pr_s}/-"
        print(f"{name:<48} {sn_disp:>26} {pr_disp:>26}  {match}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
