#!/usr/bin/env python3
"""Latency + concurrency benchmark for snowmelt PromQL range queries.

Window: a 1h span ending >=30min in the past (default end = now-30m, start = now-90m).
Driven from the contabo host against the snowmelt ClusterIP (round-robins all pods).
No third-party deps: urllib + ThreadPoolExecutor only.

Usage (run from the k3s host, or pass --base <host:9092>):
  # probe (1 req each) + warm latency + concurrency sweep on the curated set
  python3 rangebench.py --mode all
  # conc=1 latency for specific named queries
  python3 rangebench.py --mode latency --queries cgo_sum_rate,shape_sine --latency-n 20
  # concurrency sweep of one query
  python3 rangebench.py --mode concurrency --queries k_bin_vec_vec --levels 1,4,8,16,32
  # PARTITION-BALANCED wide set (one metric per partition 0-7 → spreads load
  # across all 8 owners instead of pinning one). --group wide-all = 56 queries
  # (7 kinds x 8 metrics); --group wide-sumrate = one kind across 8 partitions.
  python3 rangebench.py --mode concurrency --group wide-all --levels 10

Default --base is the contabo snowmelt ClusterIP; override for other clusters.
Latency percentiles are computed over successful (HTTP 200) responses only.
"""
import argparse, json, time, urllib.parse, urllib.request, statistics, sys
from concurrent.futures import ThreadPoolExecutor

# Representative range queries over the streaming firehose (org 0).
QUERIES = {
    "selector_light":   "shape_sine",                                                 # 100-series gauge selector
    "selector_heavy":   "rate(process_runtime_go_cgo_calls[5m])",                     # ~1450-series matrix
    "agg_scalar":       "sum(rate(process_runtime_go_cgo_calls[5m]))",                # fold to 1 series
    "agg_groupby":      "sum by (service_name) (rate(process_runtime_go_cgo_calls[5m]))",
    "agg_nodejs":       "sum(rate(process_runtime_nodejs_event_loop_lag[5m]))",
    "gauge_avg":        "avg(shape_sine)",
    # wellformed waveform generator — 8 shapes, each a 100-series gauge (series=chartNNN)
    "shape_constant":   "shape_constant",
    "shape_linear":     "shape_linear",
    "shape_sine":       "shape_sine",
    "shape_sawtooth":   "shape_sawtooth",
    "shape_square":     "shape_square",
    "shape_triangle":   "shape_triangle",
    "shape_step":       "shape_step",
    "shape_impulse":    "shape_impulse",
    # sum by (series) over each waveform — aggregation operator at 100-series cardinality
    "sumby_constant":   "sum by (series) (shape_constant)",
    "sumby_linear":     "sum by (series) (shape_linear)",
    "sumby_sine":       "sum by (series) (shape_sine)",
    "sumby_sawtooth":   "sum by (series) (shape_sawtooth)",
    "sumby_square":     "sum by (series) (shape_square)",
    "sumby_triangle":   "sum by (series) (shape_triangle)",
    "sumby_step":       "sum by (series) (shape_step)",
    "sumby_impulse":    "sum by (series) (shape_impulse)",
    # process_runtime_go_cgo_calls (~1452 series) — isolate sum-by vs rate vs cardinality
    "cgo_selector":     "process_runtime_go_cgo_calls",                                       # bare, 1452 series
    "cgo_sumby_svc":    "sum by (service_name) (process_runtime_go_cgo_calls)",               # raw sum by -> ~12
    "cgo_sumby_inst":   "sum by (service_instance_id) (process_runtime_go_cgo_calls)",        # raw sum by -> ~1452
    "cgo_sum_rate":     "sum(rate(process_runtime_go_cgo_calls[5m]))",                        # known-bad (rate+sum)
    "cgo_sumby_rate":   "sum by (service_name) (rate(process_runtime_go_cgo_calls[5m]))",     # known-bad (rate+sum by)
    # ── kinds sweep: distinct execution paths over a high-card metric (~1452 series) ──
    # window functions, no aggregation (two-hop tags-free window matrix; large output)
    "k_rate":           "rate(process_runtime_go_cgo_calls[5m])",
    "k_increase":       "increase(process_runtime_go_cgo_calls[5m])",
    "k_irate":          "irate(process_runtime_go_cgo_calls[5m])",
    "k_deriv":          "deriv(process_runtime_go_cgo_calls[5m])",
    "k_delta":          "delta(process_runtime_go_cgo_calls[5m])",
    "k_max_over_time":  "max_over_time(process_runtime_go_cgo_calls[5m])",
    "k_avg_over_time":  "avg_over_time(process_runtime_go_cgo_calls[5m])",
    "k_stddev_ovt":     "stddev_over_time(process_runtime_go_cgo_calls[5m])",
    # single-output agg over window fn (the FIXED fast path) — every agg op
    "k_avg_rate":       "avg(rate(process_runtime_go_cgo_calls[5m]))",
    "k_max_rate":       "max(rate(process_runtime_go_cgo_calls[5m]))",
    "k_min_rate":       "min(rate(process_runtime_go_cgo_calls[5m]))",
    "k_count_rate":     "count(rate(process_runtime_go_cgo_calls[5m]))",
    "k_stddev_rate":    "stddev(rate(process_runtime_go_cgo_calls[5m]))",
    "k_quantile_rate":  "quantile(0.95, rate(process_runtime_go_cgo_calls[5m]))",
    "k_sumby_ns_rate":  "sum by (k8s_namespace_name) (rate(process_runtime_go_cgo_calls[5m]))",
    "k_sumwithout_rate":"sum without (service_instance_id) (rate(process_runtime_go_cgo_calls[5m]))",
    # single-output agg over BARE selector (general tags-free + aggregate_matrix)
    "k_sum_sel":        "sum(process_runtime_go_cgo_calls)",
    "k_count_sel":      "count(process_runtime_go_cgo_calls)",
    "k_avgby_sel":      "avg by (service_name) (process_runtime_go_cgo_calls)",
    # multi-output agg (NOT the fast path — general path builds full matrix then reduces)
    "k_topk_rate":      "topk(5, rate(process_runtime_go_cgo_calls[5m]))",
    "k_bottomk_sel":    "bottomk(5, process_runtime_go_cgo_calls)",
    # binary ops
    "k_bin_vec_scalar": "rate(process_runtime_go_cgo_calls[5m]) > 100",
    "k_bin_vec_vec":    "sum by (service_name) (rate(process_runtime_go_cgo_calls[5m])) / sum by (service_name) (rate(process_runtime_go_cgo_calls[5m]))",
    "k_bin_arith":      "sum(rate(process_runtime_go_cgo_calls[5m])) + sum(rate(process_runtime_go_cgo_calls[5m]))",
    # scalar-fn wrappers over aggregation / window fn
    "k_abs_rate":       "abs(sum(rate(process_runtime_go_cgo_calls[5m])))",
    "k_clamp_rate":     "clamp_max(rate(process_runtime_go_cgo_calls[5m]), 1000000)",
    # a DIFFERENT high-card metric to confirm it's not cgo-specific
    "k_sum_nodejs":     "sum(rate(process_runtime_nodejs_event_loop_lag[5m]))",
    "k_topk_goroutine": "topk(5, process_runtime_go_goroutines)",
}

# ── WIDE metric set: one high-cardinality metric per partition (0-7), so a
# query set spreads its data-scan load across all 8 partition owners instead of
# concentrating on the single owner of process_runtime_go_cgo_calls (partition 4
# = snowmelt-4). Metric→partition confirmed via metric_metadata (org_id=0).
WIDE_METRICS = [
    ("p0", "http_server_active_requests"),               # 7127 series
    ("p1", "process_runtime_go_mem_heap_sys"),           # 3131
    ("p2", "process_runtime_go_lookups"),                # 3006
    ("p3", "process_runtime_go_mem_live_objects"),       # 3056
    ("p4", "process_runtime_go_cgo_calls"),              # 2896
    ("p5", "process_runtime_go_mem_heap_alloc"),         # 2918
    ("p6", "process_runtime_go_goroutines"),             # 2985
    ("p7", "process_runtime_nodejs_heap_size_executable"),  # 1708
]
# Query-kind templates ({m} = metric). Latency benchmark, so rate()/agg on
# gauge-ish counters is fine — we measure execution cost, not semantics.
WIDE_TEMPLATES = {
    "sel":     "{m}",
    "rate":    "rate({m}[5m])",
    "sumrate": "sum(rate({m}[5m]))",
    "sumby":   "sum by (service_name) (rate({m}[5m]))",
    "topk":    "topk(5, rate({m}[5m]))",
    "maxovt":  "max_over_time({m}[5m])",
    "binvv":   "sum by (service_name) (rate({m}[5m])) / sum by (service_name) (rate({m}[5m]))",
}
# Generate w_<template>_<partition> queries (7 templates x 8 partitions = 56).
for _tn, _tmpl in WIDE_TEMPLATES.items():
    for _pn, _metric in WIDE_METRICS:
        QUERIES[f"w_{_tn}_{_pn}"] = _tmpl.format(m=_metric)

# Convenience groups (comma-lists) for --queries.
WIDE_ALL = ",".join(f"w_{tn}_{pn}" for tn in WIDE_TEMPLATES for pn, _ in WIDE_METRICS)
# One template across all 8 partitions (balanced load, same kind):
WIDE_BY_TEMPLATE = {tn: ",".join(f"w_{tn}_{pn}" for pn, _ in WIDE_METRICS) for tn in WIDE_TEMPLATES}

def build_url(base, q, start, end, step):
    qs = urllib.parse.urlencode({"query": q, "start": start, "end": end, "step": step})
    return f"http://{base}/promql/api/v1/query_range?{qs}"

def one_request(url, timeout):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            dt = time.perf_counter() - t0
            code = r.status
            nseries = None
            try:
                d = json.loads(body)
                nseries = len(d.get("data", {}).get("result", []))
                if d.get("status") != "success":
                    code = -1  # HTTP 200 but PromQL error envelope
            except Exception:
                pass
            return dt, code, len(body), nseries
    except urllib.error.HTTPError as e:
        return time.perf_counter() - t0, e.code, 0, None
    except Exception as e:
        return time.perf_counter() - t0, 0, 0, None

def pct(xs, p):
    if not xs: return float("nan")
    xs = sorted(xs); k = (len(xs) - 1) * p / 100.0
    f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)

def summarize(results, wall):
    lat = [r[0] for r in results]
    ok = [r for r in results if r[1] == 200]
    okl = [r[0] for r in ok]
    errs = len(results) - len(ok)
    return {
        "n": len(results), "ok": len(ok), "errs": errs,
        "throughput": len(ok) / wall if wall > 0 else 0,
        "min": min(okl) if okl else float("nan"),
        "p50": pct(okl, 50), "p90": pct(okl, 90), "p95": pct(okl, 95),
        "p99": pct(okl, 99), "max": max(okl) if okl else float("nan"),
        "mean": statistics.mean(okl) if okl else float("nan"),
    }

def run_level(url, concurrency, n, timeout):
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(lambda _: one_request(url, timeout), range(n)))
    wall = time.perf_counter() - t0
    return results, wall

def run_mixed(base, names, start, end, step, concurrency, total, timeout):
    # DISTRIBUTED load: a fixed pool of `concurrency` workers, each pulling the
    # NEXT query round-robin from the whole set. So ~`concurrency` DIFFERENT
    # queries are in flight at any instant (spread across partitions + kinds) —
    # a realistic mix, NOT `concurrency` copies of one query all hammering one
    # partition owner + one ClickHouse metadata read.
    urls = [(nm, build_url(base, QUERIES[nm], start, end, step)) for nm in names]
    def worker(i):
        nm, url = urls[i % len(urls)]
        dt, code, _, _ = one_request(url, timeout)
        return (dt, code, nm)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(worker, range(total)))
    wall = time.perf_counter() - t0
    return results, wall

def fmt(s):
    return (f"n={s['n']:>3} ok={s['ok']:>3} err={s['errs']:>2}  "
            f"thr={s['throughput']:6.2f} q/s  "
            f"p50={s['p50']*1000:8.1f} p90={s['p90']*1000:8.1f} "
            f"p95={s['p95']*1000:8.1f} p99={s['p99']*1000:8.1f} "
            f"max={s['max']*1000:8.1f} ms")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="10.43.209.205:9092")
    ap.add_argument("--end-offset-min", type=int, default=30, help="end = now - this (>=30)")
    ap.add_argument("--window-min", type=int, default=60, help="span length (1h)")
    ap.add_argument("--step", default="60")
    ap.add_argument("--timeout", type=float, default=90)
    ap.add_argument("--mode", choices=["probe", "latency", "concurrency", "mixed", "all"], default="all")
    ap.add_argument("--queries", default="", help="comma list of names; default = curated set")
    ap.add_argument("--group", default="", help="wide-all | wide-<template> (e.g. wide-sumrate) — partition-balanced sets")
    ap.add_argument("--levels", default="1,2,4,8,16,32")
    ap.add_argument("--reqs-per-level", type=int, default=0, help="0 => max(4*conc,12)")
    ap.add_argument("--total", type=int, default=0, help="mixed mode: total requests (0 => 12*conc)")
    ap.add_argument("--latency-n", type=int, default=20)
    args = ap.parse_args()

    now = int(time.time())
    end = now - args.end_offset_min * 60
    start = end - args.window_min * 60
    if args.group == "wide-all":
        args.queries = WIDE_ALL
    elif args.group.startswith("wide-"):
        tmpl = args.group[len("wide-"):]
        if tmpl not in WIDE_BY_TEMPLATE:
            raise SystemExit(f"unknown group {args.group}; templates: {list(WIDE_BY_TEMPLATE)}")
        args.queries = WIDE_BY_TEMPLATE[tmpl]
    names = [x for x in args.queries.split(",") if x] or list(QUERIES.keys())
    levels = [int(x) for x in args.levels.split(",")]

    print(f"# snowmelt range-query bench")
    print(f"# base={args.base} window=[{start},{end}] "
          f"({args.window_min}m ending {args.end_offset_min}m ago) step={args.step}s "
          f"points={{(end-start)//int(float(args.step))+1}}")
    print(f"# start_utc={time.strftime('%H:%M:%S', time.gmtime(start))} "
          f"end_utc={time.strftime('%H:%M:%S', time.gmtime(end))}")

    if args.mode in ("probe", "all"):
        print("\n## PROBE (1 request each, cold)")
        for nm in names:
            url = build_url(args.base, QUERIES[nm], start, end, args.step)
            dt, code, nbytes, ns = one_request(url, args.timeout)
            print(f"  {nm:16} code={code:>4} t={dt*1000:8.1f}ms bytes={nbytes:>9} series={ns}  | {QUERIES[nm]}")

    if args.mode in ("latency", "all"):
        print(f"\n## LATENCY (sequential, n={args.latency_n}, warm)")
        for nm in names:
            url = build_url(args.base, QUERIES[nm], start, end, args.step)
            one_request(url, args.timeout)  # warmup
            results, wall = run_level(url, 1, args.latency_n, args.timeout)
            print(f"  {nm:16} {fmt(summarize(results, wall))}")

    if args.mode in ("concurrency", "all"):
        conc_names = names if args.queries else ["selector_heavy", "agg_groupby"]
        for nm in conc_names:
            url = build_url(args.base, QUERIES[nm], start, end, args.step)
            print(f"\n## CONCURRENCY SWEEP — {nm}  | {QUERIES[nm]}")
            one_request(url, args.timeout)  # warmup
            for c in levels:
                n = args.reqs_per_level or max(4 * c, 12)
                results, wall = run_level(url, c, n, args.timeout)
                print(f"  conc={c:>3}  {fmt(summarize(results, wall))}")

    if args.mode == "mixed":
        import collections
        for c in levels:
            total = args.total or 12 * c
            print(f"\n## MIXED LOAD — conc={c}, {len(names)} distinct queries round-robin, total={total} reqs")
            # warm a spread of the set so no single query pays a cold penalty
            for nm in names[:: max(1, len(names) // 8)]:
                one_request(build_url(args.base, QUERIES[nm], start, end, args.step), args.timeout)
            results, wall = run_mixed(args.base, names, start, end, args.step, c, total, args.timeout)
            print(f"  AGGREGATE  {fmt(summarize([(r[0], r[1]) for r in results], wall))}")
            byp = collections.defaultdict(list)
            for dt, code, nm in results:
                p = nm.rsplit("_", 1)[1] if nm.startswith("w_") else nm
                byp[p].append((dt, code))
            for p in sorted(byp):
                print(f"    {p:5} {fmt(summarize(byp[p], wall))}")
    print("\n# done")

if __name__ == "__main__":
    main()
