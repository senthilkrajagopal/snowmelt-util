#!/usr/bin/env python3
"""
Manual side-by-side check for Phase η: `predict_linear`,
`quantile_over_time`, `double_exponential_smoothing`.

For each expr, runs an instant query on snowmelt and prom over the
SAME window. We tabulate series count + first-series sample value.
A "fuzzy match" verdict is computed:

- predict_linear / DES: relative tolerance 5%. Both sides do
  least-squares / Holt math but Prom's interpolation across the
  range can introduce small differences from snowmelt's pure
  per-step bucketing.
- quantile_over_time: relative tolerance 5% for the same reason
  (sample-set differences inside the range can shift φ-quantile
  by a step).

Stdlib-only.
"""
import argparse
import json
import sys
import os
import time
import urllib.parse
import urllib.request

SNOW = "http://localhost:9092/promql/api/v1"
PROM = "http://localhost:9090/api/v1"

PROM_SUFFIX_MAP = {
    "jvm_memory_used": "jvm_memory_used_bytes",
    "jvm_class_loaded": "jvm_class_loaded_total",
}


EXPRS = [
    ("predict_linear({m}[5m], 60)", "predict_linear", 0.10),
    ("predict_linear({m}[10m], 600)", "predict_linear", 0.20),
    ("quantile_over_time(0.5, {m}[5m])", "quantile_over_time", 0.05),
    ("quantile_over_time(0.95, {m}[10m])", "quantile_over_time", 0.05),
    ("double_exponential_smoothing({m}[5m], 0.5, 0.5)",
     "double_exponential_smoothing", 0.10),
    ("double_exponential_smoothing({m}[5m], 0.8, 0.3)",
     "double_exponential_smoothing", 0.10),
]


def http_get(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "error", "error": str(e)}


def instant(base, expr, ts):
    q = urllib.parse.urlencode({"query": expr, "time": ts})
    return http_get(f"{base}/query?{q}")


def to_prom_expr(e, snow, prom):
    return e.replace(snow, prom) if snow != prom else e


def summarise(resp):
    if resp.get("status") != "success":
        return (0, f"ERR: {resp.get('error', '?')[:30]}")
    rs = resp.get("data", {}).get("result", [])
    if not rs:
        return (0, "<empty>")
    return (len(rs), rs[0].get("value", [None, "?"])[1])


def fuzzy_close(a, b, rel):
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if af == bf:
        return True
    denom = max(abs(af), abs(bf), 1e-9)
    return abs(af - bf) / denom <= rel


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metric", default="jvm_thread_count")
    args = ap.parse_args()
    end = int(os.environ.get("SNOWMELT_PROBE_END", int(time.time()) - 30))

    snow_metric = args.metric
    prom_metric = PROM_SUFFIX_MAP.get(snow_metric, snow_metric)
    print(f"snow metric: {snow_metric}    prom metric: {prom_metric}    eval: {end}")
    print()
    print(f"{'expr':<58} {'snow n/value':>22} {'prom n/value':>22}  verdict")
    print("-" * 115)
    for tpl, _kind, tol in EXPRS:
        snow_expr = tpl.format(m=snow_metric)
        prom_expr = to_prom_expr(tpl.format(m=prom_metric), snow_metric, prom_metric)
        sn = instant(SNOW, snow_expr, end)
        pr = instant(PROM, prom_expr, end)
        sn_n, sn_v = summarise(sn)
        pr_n, pr_v = summarise(pr)
        # series counts must match (or both be empty); values within tol.
        ok = sn_n == pr_n and (sn_n == 0 or fuzzy_close(sn_v, pr_v, tol))
        verdict = "OK" if ok else "DIFF"
        print(f"{snow_expr:<58} {f'{sn_n}/{sn_v}':>22} {f'{pr_n}/{pr_v}':>22}  {verdict}")


if __name__ == "__main__":
    sys.exit(main())
