#!/usr/bin/env python3
"""
Compare query_range latency for every panel in the Grafana dashboards
across both backends (snowmelt PromQL @ :9092, prometheus @ :9090).

For each panel it runs the same `query_range` window N times against
each backend (after one warm-up) and tabulates min / p50 / mean
latency plus the series count, with a snowmelt-vs-prometheus speedup
ratio per row and a geomean across the whole dashboard.

Counter / gauge / ratio metrics: prometheus's OTLP receiver auto-
suffixes `_total` / `_bytes` / `_ratio`, so we keep an explicit
(label, snowmelt_expr, prom_expr) tuple per panel.

Histogram metrics: prometheus stores them split into `_bucket` /
`_sum` / `_count`; the prom column queries the `rate(_count[1m])`
form so we get a meaningful number rather than always-empty.

Assumes both backends are reachable on localhost via port-forwards.
Time range defaults to `now-5m → now`, step 15s, 5 iterations.
Override via CLI flags — see `--help`.
"""
import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request

PROM = "http://localhost:9090/api/v1/query_range"
SNOW = "http://localhost:9092/promql/api/v1/query_range"

# (label, snowmelt_expr, prom_expr)
QUERIES = [
    # OTel datagen overview (20 panels)
    ("test",                          "test",                                                "test"),
    ("rate(jvm_class_loaded)",        "rate(jvm_class_loaded[1m])",                          "rate(jvm_class_loaded_total[1m])"),
    ("rate(mysql_query_count)",       "rate(mysql_query_count[1m])",                         "rate(mysql_query_count_total[1m])"),
    ("rate(redis_cmds)",              "rate(redis_commands_processed[1m])",                  "rate(redis_commands_processed_total[1m])"),
    ("rate(pg_commits)",              "rate(postgresql_commits[1m])",                        "rate(postgresql_commits_total[1m])"),
    ("rate(mongo_ops)",               "rate(mongodb_operation_count[1m])",                   "rate(mongodb_operation_count_total[1m])"),
    ("jvm_memory_used",               "jvm_memory_used",                                     "jvm_memory_used_bytes"),
    ("jvm_thread_count",              "jvm_thread_count",                                    "jvm_thread_count"),
    ("redis_memory_used",             "redis_memory_used",                                   "redis_memory_used_bytes"),
    ("process_memory_usage",          "process_memory_usage",                                "process_memory_usage_bytes"),
    ("process_go_goroutines",         "process_runtime_go_goroutines",                       "process_runtime_go_goroutines"),
    ("jvm_cpu_recent_util",           "jvm_cpu_recent_utilization",                          "jvm_cpu_recent_utilization_ratio"),
    ("process_cpu_util",              "process_cpu_utilization",                             "process_cpu_utilization_ratio"),
    ("mongodb_health",                "mongodb_health",                                      "mongodb_health"),
    ("kafka_consumer_lag",            "kafka_consumer_group_lag",                            "kafka_consumer_group_lag"),
    ("es_cluster_shards",             "elasticsearch_cluster_shards",                        "elasticsearch_cluster_shards"),
    ("jvm_gc_duration",               "jvm_gc_duration",                                     "rate(jvm_gc_duration_count[1m])"),
    ("http_server_duration",          "http_server_duration",                                "rate(http_server_duration_count[1m])"),
    ("http_client_duration",          "http_client_duration",                                "rate(http_client_duration_count[1m])"),
    ("spring_batch_job_duration",     "spring_batch_job_duration",                           "rate(spring_batch_job_duration_count[1m])"),
    # Aggregates (10 panels)
    ("sum by(cp)(jvm_mem)",           "sum by (cloud_provider) (jvm_memory_used)",           "sum by (cloud_provider) (jvm_memory_used_bytes)"),
    ("sum by(ns)(rate(mysql))",       "sum by (k8s_namespace_name) (rate(mysql_query_count[1m]))", "sum by (k8s_namespace_name) (rate(mysql_query_count_total[1m]))"),
    ("avg by(lang)(proc_cpu)",        "avg by (telemetry_sdk_language) (process_cpu_utilization)", "avg by (telemetry_sdk_language) (process_cpu_utilization_ratio)"),
    ("max by(cp)(jvm_threads)",       "max by (cloud_provider) (jvm_thread_count)",          "max by (cloud_provider) (jvm_thread_count)"),
    ("min by(cp)(redis_mem)",         "min by (cloud_provider) (redis_memory_used)",         "min by (cloud_provider) (redis_memory_used_bytes)"),
    ("count by(cp)(jvm_class)",       "count by (cloud_provider) (jvm_class_loaded)",        "count by (cloud_provider) (jvm_class_loaded_total)"),
    ("sum without(pod)(pg_commits)",  "sum without (k8s_pod_name) (postgresql_commits)",     "sum without (k8s_pod_name) (postgresql_commits_total)"),
    ("sum(rate(http_server))",        "sum(rate(http_server_duration[1m]))",                 "sum(rate(http_server_duration_count[1m]))"),
    ("avg by(svc)(rate(redis))",      "avg by (service_name) (rate(redis_commands_processed[1m]))", "avg by (service_name) (rate(redis_commands_processed_total[1m]))"),
    ("group by(cp)(kafka_lag)",       "group by (cloud_provider) (kafka_consumer_group_lag)", "group by (cloud_provider) (kafka_consumer_group_lag)"),
]


def time_one(url, query, start, end, step):
    body = urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": step}
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.load(r)
        ms = (time.perf_counter() - t0) * 1000.0
        n = len(payload.get("data", {}).get("result", []))
        return ms, n
    except Exception:
        ms = (time.perf_counter() - t0) * 1000.0
        return ms, 0


def stats(samples):
    return {
        "min": min(samples),
        "p50": statistics.median(samples),
        "mean": statistics.mean(samples),
    }


def parse_args():
    p = argparse.ArgumentParser(
        description="Compare PromQL query_range latency: snowmelt vs prometheus.",
    )
    # Time range. `--start` / `--end` win if both are set; otherwise
    # `--window-secs` is taken as `now - window-secs → now`.
    p.add_argument("--start", type=int, default=None,
                   help="Range start (Unix seconds). Use with --end. "
                        "Overrides --window-secs.")
    p.add_argument("--end", type=int, default=None,
                   help="Range end (Unix seconds). Use with --start. "
                        "Overrides --window-secs.")
    p.add_argument("--window-secs", type=int, default=300,
                   help="Range width in seconds (default 300 = 5min). "
                        "Ignored when --start/--end are both given.")
    p.add_argument("--iters", type=int, default=5,
                   help="Iterations per query per backend (default 5).")
    p.add_argument("--step", default="15",
                   help="Step in seconds or PromQL duration (default 15).")
    return p.parse_args()


def resolve_range(args):
    if args.start is not None and args.end is not None:
        if args.end <= args.start:
            raise SystemExit("--end must be > --start")
        return args.start, args.end
    if (args.start is None) != (args.end is None):
        raise SystemExit("--start and --end must be passed together")
    end = int(time.time())
    return end - args.window_secs, end


def run(start, end, step, iters):
    width = end - start
    print(f"window: start={start} end={end}  width={width}s  step={step}s  iters={iters}\n")
    print(
        f"{'Query':<32} | "
        f"{'P-mean':>7} {'P-p50':>7} {'P-min':>7} {'P-srs':>5} | "
        f"{'S-mean':>7} {'S-p50':>7} {'S-min':>7} {'S-srs':>5} | speedup"
    )
    print("-" * 130)

    rows = []
    for label, snow_q, prom_q in QUERIES:
        time_one(PROM, prom_q, start, end, step)  # warm-up
        time_one(SNOW, snow_q, start, end, step)
        prom_ms, snow_ms = [], []
        prom_n = snow_n = 0
        for _ in range(iters):
            ms, n = time_one(PROM, prom_q, start, end, step)
            prom_ms.append(ms); prom_n = n
            ms, n = time_one(SNOW, snow_q, start, end, step)
            snow_ms.append(ms); snow_n = n
        ps = stats(prom_ms); ss = stats(snow_ms)
        speedup = ps["mean"] / ss["mean"] if ss["mean"] > 0 else float("inf")
        marker = "S faster" if speedup > 1.05 else ("P faster" if speedup < 0.95 else "≈")
        print(
            f"{label:<32} | "
            f"{ps['mean']:7.1f} {ps['p50']:7.1f} {ps['min']:7.1f} {prom_n:5d} | "
            f"{ss['mean']:7.1f} {ss['p50']:7.1f} {ss['min']:7.1f} {snow_n:5d} | "
            f"{speedup:5.2f}x {marker}"
        )
        rows.append((label, ps, ss, prom_n, snow_n, speedup))

    print()
    speedups = [r[5] for r in rows if r[5] not in (0, float("inf"))]
    if speedups:
        log_sum = sum(math.log(s) for s in speedups)
        geomean = math.exp(log_sum / len(speedups))
        print(f"geomean speedup (S vs P): {geomean:.2f}x  (>1 means snowmelt faster on average)")
    s_faster = sum(1 for r in rows if r[5] > 1.05)
    p_faster = sum(1 for r in rows if r[5] < 0.95)
    tied = len(rows) - s_faster - p_faster
    print(f"snowmelt faster: {s_faster}   prometheus faster: {p_faster}   ≈tied: {tied}")


if __name__ == "__main__":
    args = parse_args()
    start, end = resolve_range(args)
    run(start, end, args.step, args.iters)
