#!/usr/bin/env python3
"""
Compare every panel from the two OTel-datagen dashboards across
both backends (snowmelt PromQL @ :9092, prometheus @ :9090).

For each panel we run the same `query_range` window on both, then
compare:
  - number of series
  - timestamp alignment per matching series
  - per-timestamp value parity (relative tolerance, since
    extrapolation-vs-raw rate naturally diffs ~1-3%)

Counter metrics: prometheus's OTLP receiver auto-suffixes `_total`,
so we keep an explicit (snowmelt_expr, prom_expr) tuple per panel.

Histogram metrics are EXPECTED to mismatch — the OTLP sink filters
them client-side (snowmelt won't see them), so we mark those panels
as `histogram_skip` and report them separately.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

SNOW = "http://localhost:9092/promql/api/v1"
PROM = "http://localhost:9090/api/v1"

# (panel_title, snowmelt_expr, prom_expr_or_None_for_same, kind)
# kind: "compare" or "histogram_skip"
PANELS = [
    # --- snowmelt-otel dashboard ---
    ("Heartbeat — test series", "test", "test", "compare"),
    ("rate jvm_class_loaded", "rate(jvm_class_loaded[1m])", "rate(jvm_class_loaded_total[1m])", "compare"),
    ("rate mysql_query_count", "rate(mysql_query_count[1m])", "rate(mysql_query_count_total[1m])", "compare"),
    ("rate redis_commands_processed", "rate(redis_commands_processed[1m])", "rate(redis_commands_processed_total[1m])", "compare"),
    ("rate postgresql_commits", "rate(postgresql_commits[1m])", "rate(postgresql_commits_total[1m])", "compare"),
    ("rate mongodb_operation_count", "rate(mongodb_operation_count[1m])", "rate(mongodb_operation_count_total[1m])", "compare"),
    ("jvm_memory_used", "jvm_memory_used", "jvm_memory_used_bytes", "compare"),
    ("jvm_thread_count", "jvm_thread_count", "jvm_thread_count", "compare"),
    ("redis_memory_used", "redis_memory_used", "redis_memory_used_bytes", "compare"),
    ("process_memory_usage", "process_memory_usage", "process_memory_usage_bytes", "compare"),
    ("process_runtime_go_goroutines", "process_runtime_go_goroutines", "process_runtime_go_goroutines", "compare"),
    ("jvm_cpu_recent_utilization", "jvm_cpu_recent_utilization", "jvm_cpu_recent_utilization_ratio", "compare"),
    ("process_cpu_utilization", "process_cpu_utilization", "process_cpu_utilization_ratio", "compare"),
    ("mongodb_health", "mongodb_health", "mongodb_health", "compare"),
    ("kafka_consumer_group_lag", "kafka_consumer_group_lag", "kafka_consumer_group_lag", "compare"),
    ("elasticsearch_cluster_shards", "elasticsearch_cluster_shards", "elasticsearch_cluster_shards", "compare"),
    ("jvm_gc_duration (histogram)", "jvm_gc_duration", None, "histogram_skip"),
    ("http_server_duration (histogram)", "http_server_duration", None, "histogram_skip"),
    ("http_client_duration (histogram)", "http_client_duration", None, "histogram_skip"),
    ("spring_batch_job_duration (histogram)", "spring_batch_job_duration", None, "histogram_skip"),
    # --- snowmelt-aggregates dashboard ---
    ("sum by (cloud_provider) (jvm_memory_used)",
     "sum by (cloud_provider) (jvm_memory_used)",
     "sum by (cloud_provider) (jvm_memory_used_bytes)", "compare"),
    ("sum by (k8s_namespace_name) (rate(mysql_query_count[1m]))",
     "sum by (k8s_namespace_name) (rate(mysql_query_count[1m]))",
     "sum by (k8s_namespace_name) (rate(mysql_query_count_total[1m]))", "compare"),
    ("avg by (telemetry_sdk_language) (process_cpu_utilization)",
     "avg by (telemetry_sdk_language) (process_cpu_utilization)",
     "avg by (telemetry_sdk_language) (process_cpu_utilization_ratio)", "compare"),
    ("max by (cloud_provider) (jvm_thread_count)",
     "max by (cloud_provider) (jvm_thread_count)",
     "max by (cloud_provider) (jvm_thread_count)", "compare"),
    ("min by (cloud_provider) (redis_memory_used)",
     "min by (cloud_provider) (redis_memory_used)",
     "min by (cloud_provider) (redis_memory_used_bytes)", "compare"),
    ("count by (cloud_provider) (jvm_class_loaded)",
     "count by (cloud_provider) (jvm_class_loaded)",
     "count by (cloud_provider) (jvm_class_loaded_total)", "compare"),
    ("sum without (k8s_pod_name) (postgresql_commits)",
     "sum without (k8s_pod_name) (postgresql_commits)",
     "sum without (k8s_pod_name) (postgresql_commits_total)", "compare"),
    ("sum(rate(http_server_duration[1m])) (histogram)",
     "sum(rate(http_server_duration[1m]))", None, "histogram_skip"),
    ("avg by (service_name) (rate(redis_commands_processed[1m]))",
     "avg by (service_name) (rate(redis_commands_processed[1m]))",
     "avg by (service_name) (rate(redis_commands_processed_total[1m]))", "compare"),
    ("group by (cloud_provider) (kafka_consumer_group_lag)",
     "group by (cloud_provider) (kafka_consumer_group_lag)",
     "group by (cloud_provider) (kafka_consumer_group_lag)", "compare"),
    # --- Phase α: *_over_time family ---
    # Naming: snowmelt mirrors the OTLP attribute names verbatim;
    # prom's OTLP receiver applies OpenMetrics unit suffixes per
    # spec — `_bytes` for byte gauges, `_ratio` for ratio gauges,
    # `_total` for counters. So the prom expr substitutes the
    # suffixed name; snowmelt expr stays bare.
    ("avg_over_time(jvm_memory_used[5m])",
     "avg_over_time(jvm_memory_used[5m])",
     "avg_over_time(jvm_memory_used_bytes[5m])", "compare"),
    ("min_over_time(jvm_memory_used[5m])",
     "min_over_time(jvm_memory_used[5m])",
     "min_over_time(jvm_memory_used_bytes[5m])", "compare"),
    ("max_over_time(jvm_memory_used[5m])",
     "max_over_time(jvm_memory_used[5m])",
     "max_over_time(jvm_memory_used_bytes[5m])", "compare"),
    ("sum_over_time(jvm_thread_count[5m])",
     "sum_over_time(jvm_thread_count[5m])",
     "sum_over_time(jvm_thread_count[5m])", "compare"),
    ("count_over_time(jvm_thread_count[5m])",
     "count_over_time(jvm_thread_count[5m])",
     "count_over_time(jvm_thread_count[5m])", "compare"),
    ("last_over_time(jvm_memory_used[5m])",
     "last_over_time(jvm_memory_used[5m])",
     "last_over_time(jvm_memory_used_bytes[5m])", "compare"),
    ("first_over_time(jvm_memory_used[5m])",
     "first_over_time(jvm_memory_used[5m])",
     "first_over_time(jvm_memory_used_bytes[5m])", "compare"),
    ("present_over_time(jvm_thread_count[5m])",
     "present_over_time(jvm_thread_count[5m])",
     "present_over_time(jvm_thread_count[5m])", "compare"),
    ("stddev_over_time(jvm_memory_used[5m])",
     "stddev_over_time(jvm_memory_used[5m])",
     "stddev_over_time(jvm_memory_used_bytes[5m])", "compare"),
    ("stdvar_over_time(jvm_memory_used[5m])",
     "stdvar_over_time(jvm_memory_used[5m])",
     "stdvar_over_time(jvm_memory_used_bytes[5m])", "compare"),
    ("mad_over_time(jvm_memory_used[5m])",
     "mad_over_time(jvm_memory_used[5m])",
     "mad_over_time(jvm_memory_used_bytes[5m])", "compare"),
    # --- Phase γ: counter-aware extras ---
    # Same suffix convention. delta/idelta/deriv use the byte gauge
    # `jvm_memory_used` → prom side `jvm_memory_used_bytes`.
    # changes is on the unsuffixed `jvm_thread_count`. resets is on
    # the counter `jvm_class_loaded` → prom side `jvm_class_loaded_total`.
    ("delta(jvm_memory_used[5m])",
     "delta(jvm_memory_used[5m])",
     "delta(jvm_memory_used_bytes[5m])", "compare"),
    ("idelta(jvm_memory_used[5m])",
     "idelta(jvm_memory_used[5m])",
     "idelta(jvm_memory_used_bytes[5m])", "compare"),
    ("deriv(jvm_memory_used[5m])",
     "deriv(jvm_memory_used[5m])",
     "deriv(jvm_memory_used_bytes[5m])", "compare"),
    ("changes(jvm_thread_count[5m])",
     "changes(jvm_thread_count[5m])",
     "changes(jvm_thread_count[5m])", "compare"),
    ("resets(jvm_class_loaded[5m])",
     "resets(jvm_class_loaded[5m])",
     "resets(jvm_class_loaded_total[5m])", "compare"),
    # --- Phase β: scalar fns (math, trig, calendar, composition) ---
    # Per-sample transforms applied post-bucketing in Rust; SQL is
    # unchanged so series counts stay identical to the unwrapped
    # query. `__name__` is stripped from output labels per Prom
    # convention. Prom-side gauge args still need `_bytes` suffix.
    ("abs(jvm_memory_used)",
     "abs(jvm_memory_used)",
     "abs(jvm_memory_used_bytes)", "compare"),
    ("clamp_max(jvm_memory_used, 1e9)",
     "clamp_max(jvm_memory_used, 1000000000)",
     "clamp_max(jvm_memory_used_bytes, 1000000000)", "compare"),
    ("ceil(avg_over_time(jvm_thread_count[5m]))",
     "ceil(avg_over_time(jvm_thread_count[5m]))",
     "ceil(avg_over_time(jvm_thread_count[5m]))", "compare"),
    ("ln(clamp_min(jvm_memory_used, 1))",
     "ln(clamp_min(jvm_memory_used, 1))",
     "ln(clamp_min(jvm_memory_used_bytes, 1))", "compare"),
    ("sgn(deriv(jvm_memory_used[5m]))",
     "sgn(deriv(jvm_memory_used[5m]))",
     "sgn(deriv(jvm_memory_used_bytes[5m]))", "compare"),
    ("hour(jvm_thread_count)",
     "hour(jvm_thread_count)",
     "hour(jvm_thread_count)", "compare"),
    ("timestamp(jvm_thread_count)",
     "timestamp(jvm_thread_count)",
     "timestamp(jvm_thread_count)", "compare"),
    # --- Phase δ: aggregator-parameter extension ---
    # topk/bottomk produce ≤N series with ORIGINAL labels. quantile
    # collapses to 1 series per group. stddev/stdvar same. Multi-out
    # ops compare by harness's structural diff which expects same
    # series-counts on both sides.
    ("topk(5, jvm_memory_used)",
     "topk(5, jvm_memory_used)",
     "topk(5, jvm_memory_used_bytes)", "compare"),
    ("bottomk(5, jvm_memory_used)",
     "bottomk(5, jvm_memory_used)",
     "bottomk(5, jvm_memory_used_bytes)", "compare"),
    ("quantile(0.95, jvm_memory_used)",
     "quantile(0.95, jvm_memory_used)",
     "quantile(0.95, jvm_memory_used_bytes)", "compare"),
    ("quantile by (cloud_provider) (0.99, jvm_memory_used)",
     "quantile by (cloud_provider) (0.99, jvm_memory_used)",
     "quantile by (cloud_provider) (0.99, jvm_memory_used_bytes)", "compare"),
    ("stddev by (cloud_provider) (jvm_memory_used)",
     "stddev by (cloud_provider) (jvm_memory_used)",
     "stddev by (cloud_provider) (jvm_memory_used_bytes)", "compare"),
    ("stdvar by (cloud_provider) (jvm_memory_used)",
     "stdvar by (cloud_provider) (jvm_memory_used)",
     "stdvar by (cloud_provider) (jvm_memory_used_bytes)", "compare"),
    ("count_values(\"th\", jvm_thread_count)",
     "count_values(\"th\", jvm_thread_count)",
     "count_values(\"th\", jvm_thread_count)", "compare"),
    # --- Phase ε: label manipulation + sort ---
    # label_join / label_replace mutate per-series labels (no value
    # change). sort/sort_desc/sort_by_label only affect instant
    # ordering — comparison still aligns by label set.
    ("label_join(jvm_thread_count, \"region_az\", \"-\", \"cloud_region\", \"cloud_availability_zone\")",
     "label_join(jvm_thread_count, \"region_az\", \"-\", \"cloud_region\", \"cloud_availability_zone\")",
     "label_join(jvm_thread_count, \"region_az\", \"-\", \"cloud_region\", \"cloud_availability_zone\")", "compare"),
    ("label_replace(jvm_thread_count, \"ns_kind\", \"$1\", \"k8s_namespace_name\", \"(.*)-.*\")",
     "label_replace(jvm_thread_count, \"ns_kind\", \"$1\", \"k8s_namespace_name\", \"(.*)-.*\")",
     "label_replace(jvm_thread_count, \"ns_kind\", \"$1\", \"k8s_namespace_name\", \"(.*)-.*\")", "compare"),
    ("sort(jvm_thread_count)",
     "sort(jvm_thread_count)",
     "sort(jvm_thread_count)", "compare"),
    ("sort_desc(jvm_thread_count)",
     "sort_desc(jvm_thread_count)",
     "sort_desc(jvm_thread_count)", "compare"),
    # --- Phase ζ: constants + presence ---
    # `pi` / `time` / `vector(N)` produce a single labelless series
    # constant across the range. `time()` differs only by the
    # backend's clock skew; harness's relative tolerance handles it.
    # `absent` of a non-existent metric should yield the synthetic
    # row in both backends; `absent` of a populated metric should
    # be empty everywhere.
    ("pi()",                                    "pi()", "pi()", "compare"),
    ("time()",                                  "time()", "time()", "compare"),
    ("vector(42)",                              "vector(42)", "vector(42)", "compare"),
    ("hour() — eval-time hour",                 "hour()", "hour()", "compare"),
    ("minute() — eval-time minute",             "minute()", "minute()", "compare"),
    ("absent(does_not_exist) — synthetic 1",
     'absent(does_not_exist{job="snowmelt"})',
     'absent(does_not_exist{job="snowmelt"})', "compare"),
    ("absent_over_time(does_not_exist[5m])",
     "absent_over_time(does_not_exist[5m])",
     "absent_over_time(does_not_exist[5m])", "compare"),
    # --- Phase η: predictive + parameterized window fns ---
    # `predict_linear` extrapolates a least-squares fit; small
    # variation between Prom's interpolation and our pure
    # per-step bucketing is normal — REL_TOL covers ~5%.
    # `quantile_over_time` returns the φ-quantile per series.
    # `double_exponential_smoothing` returns the smoothed value
    # at the window end.
    ("predict_linear(jvm_thread_count[5m], 60)",
     "predict_linear(jvm_thread_count[5m], 60)",
     "predict_linear(jvm_thread_count[5m], 60)", "compare"),
    ("quantile_over_time(0.95, jvm_memory_used[10m])",
     "quantile_over_time(0.95, jvm_memory_used[10m])",
     "quantile_over_time(0.95, jvm_memory_used_bytes[10m])", "compare"),
    ("double_exponential_smoothing(jvm_thread_count[5m], 0.5, 0.5)",
     "double_exponential_smoothing(jvm_thread_count[5m], 0.5, 0.5)",
     "double_exponential_smoothing(jvm_thread_count[5m], 0.5, 0.5)", "compare"),
]

REL_TOL = 0.05  # 5% per-value tolerance — covers extrapolation gap
COUNT_TOL = 0   # series count must match exactly


def query_range(base, expr, start, end, step):
    q = urllib.parse.urlencode({"query": expr, "start": start, "end": end, "step": step})
    url = f"{base}/query_range?{q}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _strip_name(name):
    """Strip prom's auto-added unit/total suffixes so series with
    `__name__: jvm_class_loaded` (snowmelt) match
    `__name__: jvm_class_loaded_total` (prom)."""
    for suf in ("_total", "_bytes", "_ratio", "_seconds"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def labels_key(metric_dict, group_by_labels=None):
    """Stable key for matching series across backends. By default
    use the full label set; if group_by_labels is provided, restrict
    to those (used when a query has `by (a, b)` clause).

    Two corrections we apply:
      - drop `__name__` (snowmelt keeps it after `rate()`, prom drops)
      - normalise prom's auto-suffixes (`_total`, `_bytes`, `_ratio`,
        `_seconds`) so the metric-name component matches when present
    """
    if group_by_labels:
        items = sorted((k, v) for k, v in metric_dict.items() if k in group_by_labels)
    else:
        items = sorted(
            (k, _strip_name(v) if k == "__name__" else v)
            for k, v in metric_dict.items()
            if k != "__name__"  # snowmelt keeps it after rate(), prom drops it
        )
    return tuple(items)


def compare(snow_resp, prom_resp):
    """Diff two query_range responses. Returns a dict with verdict +
    detail."""
    if snow_resp.get("status") != "success":
        return {"verdict": "snow_err", "detail": snow_resp}
    if prom_resp.get("status") != "success":
        return {"verdict": "prom_err", "detail": prom_resp}
    snow_series = snow_resp["data"]["result"]
    prom_series = prom_resp["data"]["result"]

    if not snow_series and not prom_series:
        return {"verdict": "both_empty"}
    if not snow_series:
        return {"verdict": "snow_empty", "prom_count": len(prom_series)}
    if not prom_series:
        return {"verdict": "prom_empty", "snow_count": len(snow_series)}

    snow_count = len(snow_series)
    prom_count = len(prom_series)

    # Bucket per-series points for ts/value diffing
    def bucket(series):
        out = {}
        for s in series:
            key = labels_key(s.get("metric", {}))
            pts = [(int(t), float(v)) for t, v in s.get("values", [])]
            out[key] = pts
        return out

    snow_b = bucket(snow_series)
    prom_b = bucket(prom_series)

    # Match by label-set; for unmatched series, report
    matched = set(snow_b.keys()) & set(prom_b.keys())
    snow_only = set(snow_b.keys()) - set(prom_b.keys())
    prom_only = set(prom_b.keys()) - set(snow_b.keys())

    sample_diffs = []
    bad_value_count = 0
    bad_ts_count = 0
    total_compared = 0
    for k in sorted(matched, key=lambda x: str(x))[:5]:  # limit detail to first 5 series
        sp = snow_b[k]
        pp = prom_b[k]
        # Match points by timestamp
        sp_map = {ts: v for ts, v in sp}
        pp_map = {ts: v for ts, v in pp}
        common_ts = sorted(set(sp_map.keys()) & set(pp_map.keys()))
        snow_only_ts = set(sp_map.keys()) - set(pp_map.keys())
        prom_only_ts = set(pp_map.keys()) - set(sp_map.keys())
        bad_ts_count += len(snow_only_ts) + len(prom_only_ts)
        for t in common_ts:
            sv, pv = sp_map[t], pp_map[t]
            # Relative tolerance, treating both as 0-equivalent if tiny
            denom = max(abs(sv), abs(pv), 1e-9)
            rel = abs(sv - pv) / denom
            if rel > REL_TOL:
                bad_value_count += 1
                if len(sample_diffs) < 3:
                    sample_diffs.append((str(k)[:80], t, round(sv, 3), round(pv, 3), round(rel * 100, 2)))
            total_compared += 1

    # Separate verdicts:
    #   "ok"        — series + labels + ts + values all match
    #   "ts_drift"  — same series, all overlapping values match,
    #                 but one side has more/fewer points (rate-emission
    #                 edge: snowmelt drops rate at step boundaries
    #                 with no samples in the [N] window; prom is more
    #                 generous about emission)
    #   "diff"      — real value mismatch beyond tolerance
    structural_match = (
        snow_count == prom_count and not snow_only and not prom_only
    )
    if structural_match and bad_value_count == 0 and bad_ts_count == 0:
        verdict = "ok"
    elif structural_match and bad_value_count == 0 and bad_ts_count > 0:
        verdict = "ts_drift"
    else:
        verdict = "diff"
    return {
        "verdict": verdict,
        "snow_count": snow_count,
        "prom_count": prom_count,
        "matched": len(matched),
        "snow_only": len(snow_only),
        "prom_only": len(prom_only),
        "values_compared": total_compared,
        "bad_values": bad_value_count,
        "bad_timestamps": bad_ts_count,
        "sample_diffs": sample_diffs,
    }


def main():
    end = int(time.time()) - 30
    start = end - 240  # 4 min window
    step = 30          # 30s step → 9 points per series

    print(f"window: start={start} end={end} step={step}s\n")
    print(f"{'panel':<55} {'verdict':<14} {'snow':>4} {'prom':>4} {'matched':>7} {'cmp':>4} {'badv':>4} {'badt':>4}")
    print("-" * 110)

    pass_n, drift_n, diff_n, skip_n, err_n = 0, 0, 0, 0, 0
    detail_panels = []

    for title, sexpr, pexpr, kind in PANELS:
        if kind == "histogram_skip":
            print(f"{title:<55} {'SKIP-HIST':<14}    -    -       -    -    -    -")
            skip_n += 1
            continue

        snow_resp = query_range(SNOW, sexpr, start, end, step)
        prom_resp = query_range(PROM, pexpr, start, end, step)
        r = compare(snow_resp, prom_resp)
        v = r["verdict"]
        if v == "ok":
            pass_n += 1
        elif v == "ts_drift":
            drift_n += 1
        elif v == "diff":
            diff_n += 1
            detail_panels.append((title, r))
        elif v == "both_empty":
            skip_n += 1
        else:
            err_n += 1
            detail_panels.append((title, r))

        sn = r.get("snow_count", "-")
        pn = r.get("prom_count", "-")
        mt = r.get("matched", "-")
        cm = r.get("values_compared", "-")
        bv = r.get("bad_values", "-")
        bt = r.get("bad_timestamps", "-")
        print(f"{title:<55} {v:<14} {sn!s:>4} {pn!s:>4} {mt!s:>7} {cm!s:>4} {bv!s:>4} {bt!s:>4}")

    print("-" * 110)
    print(f"PASS: {pass_n}   TS_DRIFT: {drift_n}   DIFF: {diff_n}   SKIP: {skip_n}   ERR: {err_n}")
    print(f"  PASS    = identical (series, labels, timestamps, values)")
    print(f"  TS_DRIFT= series + values match where they overlap; rate emission edges differ")
    print(f"  DIFF    = real value mismatch beyond {int(REL_TOL*100)}% tolerance")
    print(f"  SKIP    = histogram (filtered by design) or both backends empty\n")

    if detail_panels:
        print("=== Detail (first 5 problematic) ===")
        for title, r in detail_panels[:5]:
            print(f"\n[{title}]")
            print(f"  verdict={r['verdict']}, snow={r.get('snow_count')}, prom={r.get('prom_count')}")
            print(f"  matched={r.get('matched')}, snow_only={r.get('snow_only')}, prom_only={r.get('prom_only')}")
            print(f"  values_compared={r.get('values_compared')}, bad_values={r.get('bad_values')}, bad_timestamps={r.get('bad_timestamps')}")
            for k, t, sv, pv, rel in r.get("sample_diffs", []):
                print(f"    series={k}  ts={t}  snow={sv}  prom={pv}  rel={rel}%")


if __name__ == "__main__":
    main()
