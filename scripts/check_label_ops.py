#!/usr/bin/env python3
"""
Manual side-by-side check for the Phase ε label-manipulation +
sort fns. Each expr runs as an instant query against snowmelt and
prom; for label-mutating ops we compare series count + pick a
representative output label value across both backends.

Usage: python3 scripts/check_label_ops.py [--metric NAME]

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
    ("label_join(thread_count, region_az, -, …)",
     'label_join({m}, "region_az", "-", "cloud_region", "cloud_availability_zone")',
     "region_az"),
    ("label_replace(jvm_thread_count, ns_kind, $1, …)",
     'label_replace({m}, "ns_kind", "$1", "k8s_namespace_name", "(.*)-.*")',
     "ns_kind"),
    ("sort(jvm_thread_count)", "sort({m})", None),
    ("sort_desc(jvm_thread_count)", "sort_desc({m})", None),
    ("sort_by_label(jvm_thread_count, cloud_provider)",
     'sort_by_label({m}, "cloud_provider")', None),
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


def to_prom_expr(e):
    out = e
    for s, p in PROM_SUFFIX_MAP.items():
        if s == p or p in out:
            continue
        out = out.replace(s, p)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metric", default="jvm_thread_count")
    args = ap.parse_args()
    end = int(os.environ.get("SNOWMELT_PROBE_END", int(time.time()) - 30))

    snow_metric = args.metric
    prom_metric = PROM_SUFFIX_MAP.get(snow_metric, snow_metric)
    print(f"snow metric: {snow_metric}    prom metric: {prom_metric}    eval: {end}")
    print()
    print(f"{'op':<48} {'snow ser/probe':>22} {'prom ser/probe':>22}  match?")
    print("-" * 100)
    for name, tpl, probe_label in EXPRS:
        snow_expr = tpl.format(m=snow_metric)
        prom_expr = to_prom_expr(tpl.format(m=prom_metric))
        sn = instant(SNOW, snow_expr, end)
        pr = instant(PROM, prom_expr, end)

        def summarise(resp):
            if resp.get("status") != "success":
                return (0, f"ERR: {resp.get('error', '?')[:30]}")
            rs = resp.get("data", {}).get("result", [])
            if not rs:
                return (0, "<empty>")
            if probe_label:
                # Show the new/manipulated label's value on the first series.
                v = rs[0].get("metric", {}).get(probe_label, "<no label>")
            else:
                # Show the value of the first series (sort ops).
                v = rs[0].get("value", [None, "?"])[1]
            return (len(rs), v)

        sn_n, sn_v = summarise(sn)
        pr_n, pr_v = summarise(pr)
        match = "OK" if sn_n == pr_n and str(sn_v) == str(pr_v) else "DIFF"
        print(f"{name:<48} {f'{sn_n}/{sn_v}':>22} {f'{pr_n}/{pr_v}':>22}  {match}")


if __name__ == "__main__":
    sys.exit(main())
