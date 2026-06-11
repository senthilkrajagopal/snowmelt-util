#!/usr/bin/env python3
"""
Manual side-by-side check for the Phase ζ constants + presence
fns. Each expr runs as an instant query against snowmelt and
prom; we compare series count + value.

Usage: python3 scripts/check_constants_absent.py

Stdlib-only.
"""
import argparse
import json
import math
import sys
import os
import time
import urllib.parse
import urllib.request

SNOW = "http://localhost:9092/promql/api/v1"
PROM = "http://localhost:9090/api/v1"


# (label, expr, expected behaviour). For `time()` and `hour()`/`minute()`
# the value is a function of `eval_time`; we accept any value within a
# 2 s tolerance of the corresponding component computed from `eval_time`.
EXPRS = [
    ("pi()", "pi()", "constant"),
    ("time()", "time()", "time"),
    ("vector(42)", "vector(42)", "constant"),
    ("hour()", "hour()", "calendar"),
    ("minute()", "minute()", "calendar"),
    ("absent(jvm_thread_count) — should be empty",
     "absent(jvm_thread_count)", "absent_empty"),
    ("absent(does_not_exist) — synthetic 1",
     'absent(does_not_exist{job="snowmelt"})', "absent_one"),
    ("absent_over_time(does_not_exist[5m])",
     "absent_over_time(does_not_exist[5m])", "absent_one"),
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


def summarise(resp):
    """Return (n_series, first_value_or_empty). Handles both
    resultType=vector (list of {metric, value}) and resultType=scalar
    ([ts, "v"]) — Prom returns pi()/time()/vector(N) as scalar,
    snowmelt returns them as a labelless vector."""
    if resp.get("status") != "success":
        return (0, f"ERR: {resp.get('error', '?')[:30]}")
    data = resp.get("data", {})
    rs = data.get("result", [])
    rtype = data.get("resultType")
    if rtype == "scalar":
        # rs is [ts, "v"] — count as 1 series.
        if isinstance(rs, list) and len(rs) == 2:
            return (1, rs[1])
        return (0, "<empty>")
    if not rs:
        return (0, "<empty>")
    return (len(rs), rs[0].get("value", [None, "?"])[1])


def value_close(a, b, tol):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    end = int(os.environ.get("SNOWMELT_PROBE_END", int(time.time()) - 30))

    print(f"eval: {end}")
    print()
    print(f"{'op':<55} {'snow n/value':>22} {'prom n/value':>22}  verdict")
    print("-" * 110)
    for name, expr, kind in EXPRS:
        sn = instant(SNOW, expr, end)
        pr = instant(PROM, expr, end)
        sn_n, sn_v = summarise(sn)
        pr_n, pr_v = summarise(pr)

        if kind == "constant":
            ok = sn_n == 1 and pr_n == 1 and value_close(sn_v, pr_v, 1e-9)
        elif kind == "time":
            # Time can drift a few seconds between server clocks /
            # eval ts. Accept ±2 s.
            ok = (sn_n == 1 and pr_n == 1
                  and value_close(sn_v, pr_v, 2.0))
        elif kind == "calendar":
            # Component value depends on eval ts. Accept exact match
            # OR ±1 (boundary tolerance: hour/minute can wrap).
            try:
                a = int(float(sn_v))
                b = int(float(pr_v))
                ok = sn_n == 1 and pr_n == 1 and abs(a - b) <= 1
            except (TypeError, ValueError):
                ok = False
        elif kind == "absent_empty":
            ok = sn_n == 0 and pr_n == 0
        elif kind == "absent_one":
            ok = sn_n == 1 and pr_n == 1 and value_close(sn_v, pr_v, 1e-9)
        else:
            ok = False
        verdict = "OK" if ok else "DIFF"
        print(f"{name:<55} {f'{sn_n}/{sn_v}':>22} {f'{pr_n}/{pr_v}':>22}  {verdict}")


if __name__ == "__main__":
    sys.exit(main())
