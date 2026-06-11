# scripts/

> **Repo split:** these scripts moved here from the main `snowmelt` repo
> (2026-06). The contabo deploy scripts still build/install the runtime from a
> `snowmelt` checkout (`/root/snowmelt` on the box) and reference this repo's
> charts/values via `SNOWMELT_UTIL` (default `/root/snowmelt-util`) — check
> out both repos side by side.

Pure-Python (stdlib-only) tools for sanity-checking and benchmarking
snowmelt's PromQL surface against upstream Prometheus side-by-side.
All scripts assume the in-cluster snowmelt PromQL endpoint and
Prometheus endpoint are reachable on localhost — typically via
`kubectl port-forward`:

```bash
kubectl port-forward svc/snowmelt 9092:9092                  # snowmelt PromQL
kubectl port-forward svc/snowmelt-prometheus-server 9090:80  # prometheus
```

## Naming convention — snowmelt vs prometheus metric names

Prometheus's OTLP receiver applies **OpenMetrics-style unit suffixes**
to incoming metrics per spec — counters get `_total`, byte gauges get
`_bytes`, ratio gauges get `_ratio`, second gauges get `_seconds`.
Snowmelt mirrors the original OTLP attribute names verbatim, so the
same metric appears under different names on each backend:

| Snowmelt | Prometheus |
|---|---|
| `jvm_class_loaded` | `jvm_class_loaded_total` |
| `mysql_query_count` | `mysql_query_count_total` |
| `redis_commands_processed` | `redis_commands_processed_total` |
| `postgresql_commits` | `postgresql_commits_total` |
| `mongodb_operation_count` | `mongodb_operation_count_total` |
| `jvm_memory_used` | `jvm_memory_used_bytes` |
| `jvm_memory_committed` | `jvm_memory_committed_bytes` |
| `redis_memory_used` | `redis_memory_used_bytes` |
| `process_memory_usage` | `process_memory_usage_bytes` |
| `jvm_cpu_recent_utilization` | `jvm_cpu_recent_utilization_ratio` |
| `process_cpu_utilization` | `process_cpu_utilization_ratio` |
| `jvm_thread_count` | `jvm_thread_count` (unitless — no suffix) |
| `process_runtime_go_goroutines` | same |
| `kafka_consumer_group_lag` | same |
| `elasticsearch_cluster_shards` | same |

Every script in this directory bakes this map in via `PROM_SUFFIX_MAP`
and auto-derives the prom-side name from the snowmelt-side default.
Pass `--prom-metric` (or `--prom-gauge` / `--prom-counter` where
applicable) to override when probing a metric outside the map.

The Grafana dashboards do the same — `*-prometheus.yaml` dashboards
hardcode the suffixed names; `*-snowmelt.yaml` dashboards use the
bare names.

## `compare_dashboards.py` — correctness comparison

Walks every dashboard panel and compares snowmelt's response against
prometheus for the same window: series count, timestamp alignment,
and per-timestamp values (with a small relative tolerance — rate
extrapolation in upstream Prom naturally diffs ~1–3% from raw rate
math). Histograms are flagged and skipped. Use when validating a
`query` crate change end-to-end against a live cluster.

The `PANELS` list is the canonical fixture: each row is
`(display_name, snow_expr, prom_expr, "compare" | "histogram_skip")`.
The snow_expr and prom_expr are stored independently — that's how
the OpenMetrics suffix differences (see "Naming convention" above)
get reconciled. Per-phase counts so far:

- Original 8 dashboards (rate/aggregate panels): ~30 rows
- Phase α (`*_over_time` family): 11 rows
- Phase γ (counter-aware extras): 5 rows
- Phase β (scalar fns): 7 representative rows
- Phase δ (aggregator parameters): 8 rows
- Phase ε (label ops + sort): 4 rows
- Phase ζ (constants + presence): 7 rows
- Phase η (predictive + parameterized): 3 rows

After landing a new PromQL phase, append the new exprs to `PANELS`
so the harness exercises them automatically.

```bash
python3 scripts/compare_dashboards.py
```

## `check_over_time.py` — Phase α manual probe

Targeted side-by-side check for the `*_over_time` family — runs each
of the 11 functions as both an instant query AND a range query
against snowmelt + prometheus, prints a single-screen report.

Use when `compare_dashboards.py` flags a verdict that needs eyeballs
on the actual values (timestamps, counts, sample at first step) for
just the over_time fns; lower noise than re-running the full panel
suite.

```bash
# Defaults: metric=jvm_memory_used → auto-suffixed to jvm_memory_used_bytes
# on the prom side via PROM_SUFFIX_MAP. Range=5m, step=30s, window=5m.
python3 scripts/check_over_time.py

# Override the snowmelt metric (`--metric`); the prom metric is
# auto-derived. Pass `--prom-metric` only when probing a metric
# outside PROM_SUFFIX_MAP.
python3 scripts/check_over_time.py --metric jvm_thread_count
python3 scripts/check_over_time.py --metric some_custom_gauge \
                                   --prom-metric some_custom_gauge_bytes

# Wider range, finer step, longer history
python3 scripts/check_over_time.py --range 10m --step 15s --window 30m
```

Output shape (header confirms which prom name was auto-derived):

```
snow metric: jvm_memory_used    prom metric: jvm_memory_used_bytes
range arg: [5m]    step: 30s    window: 300s
snowmelt: http://localhost:9092/promql/api/v1    prometheus: http://localhost:9090/api/v1

=== instant queries (single value at end-30s) ===
function                          snow           prom  match?
----------------------------------------------------------------------
avg_over_time         3.109e+08      3.109e+08    OK
min_over_time         1.100e+08      1.100e+08    OK
…

=== range queries (start=… end=… step=30s) ===
function              snow ser/pts/v0    prom ser/pts/v0
----------------------------------------------------------------------
avg_over_time             6/9/1.23e+08      6/9/1.23e+08
…
```

`OK` if both sides agree to within 5% on the instant value; `DIFF`
otherwise (look at the range section for where they diverge).

## `check_counter_extras.py` — Phase γ manual probe

Same shape as `check_over_time.py` but for the Phase γ
counter-aware extras: `delta`, `idelta`, `deriv` (gauge tools),
`changes`, `resets` (counter tools).

The script keeps the gauge / counter metrics separate via FOUR flags
(snow + prom variants of each) since the math expects different
inputs and prom's suffix differs per metric kind. Defaults
auto-derive prom names via `PROM_SUFFIX_MAP`.

```bash
# Defaults:
#   gauge=jvm_memory_used   → auto: prom=jvm_memory_used_bytes
#   counter=jvm_class_loaded → auto: prom=jvm_class_loaded_total
python3 scripts/check_counter_extras.py

# Override individually
python3 scripts/check_counter_extras.py --gauge redis_memory_used
python3 scripts/check_counter_extras.py --counter mysql_query_count
python3 scripts/check_counter_extras.py --gauge custom_gauge \
                                        --prom-gauge custom_gauge_bytes

# Wider window, longer history
python3 scripts/check_counter_extras.py --range 10m --step 30s --window 30m
```

Output shape: same as `check_over_time.py` — header confirms the
snow/prom metric pair, then an instant-value matrix
(snow vs prom + OK/DIFF verdict) followed by a range-query
ser/pts/v0 summary per fn.

## `check_scalar_fns.py` — Phase β manual probe

Runs ~24 representative scalar-fn exprs (math, trig, calendar, plus
composition with rate/over_time/aggregate) against snowmelt + prom
as instant queries; prints a per-expr OK/DIFF verdict.

Doesn't exhaustively cover all 36 Phase β functions — see the
per-module unit tests in `engine/src/promql_udf/scalar_fns.rs` for
that. This script is for live behaviour check post-deploy.

Composition exprs (e.g. `abs(rate(jvm_class_loaded[1m]))`)
hardcode metric names that aren't `--metric`. The prom-side expr
gets a lexical pass via `to_prom_expr` that substitutes any known
snowmelt metric name with its prom-suffixed counterpart, so
the user only needs to override `--metric` (and optionally
`--prom-metric`) for the math/trig/calendar arg.

```bash
# Defaults: metric=jvm_memory_used → auto-suffixed to jvm_memory_used_bytes
python3 scripts/check_scalar_fns.py

# Override the snowmelt metric
python3 scripts/check_scalar_fns.py --metric jvm_thread_count

# Override the prom-side too (rare — only when the metric isn't in PROM_SUFFIX_MAP)
python3 scripts/check_scalar_fns.py --metric custom \
                                    --prom-metric custom_bytes
```

Expected output (header confirms the snow/prom metric pair):

```
snow metric: jvm_memory_used    prom metric: jvm_memory_used_bytes    range: [5m]    eval: …
snowmelt: http://localhost:9092/promql/api/v1    prometheus: http://localhost:9090/api/v1

expression                                       snow           prom  match?
------------------------------------------------------------------------------------
abs                                          3.1e+08        3.1e+08    OK
ceil                                         3.1e+08        3.1e+08    OK
…
abs(rate(jvm_class_loaded[1m]))               240.4          241.1    OK
floor(avg_over_time(jvm_thread_count[5m]))    8              8        OK
sgn(deriv(jvm_memory_used[5m]))               1              1        OK
```

`OK` if both sides agree to within 5%. Outliers usually trace to:
- DIFF on `rate`/`deriv` composition: rate-extrapolation gap
  documented in §"Known semantic gaps" of `query/translations.md`.
- DIFF on `ln`/`log*`: NaN propagation differences for negative
  inputs.
- DIFF on calendar fns: only if pod / system clock is wildly out
  of sync.

## `check_predictive.py` — Phase η manual probe

Spot-check for the parameterized window fns: `predict_linear` (linear
extrapolation), `quantile_over_time` (per-series φ-quantile in the
range), and `double_exponential_smoothing` (Holt's terminal value).

Each expr runs as an instant query against snowmelt and prom; values
are compared with a per-fn relative tolerance (5–20%) since both
backends interpolate the math slightly differently inside the
range.

```bash
python3 scripts/check_predictive.py
python3 scripts/check_predictive.py --metric jvm_memory_used
```

Output shape:

```
expr                                                       snow n/value          prom n/value  verdict
-------------------------------------------------------------------------------------------------------------------
predict_linear(jvm_thread_count[5m], 60)                          16/47.2             16/47.5  OK
quantile_over_time(0.95, jvm_thread_count[10m])                   16/45.1             16/45.0  OK
double_exponential_smoothing(jvm_thread_count[5m], 0.5, 0.5)      16/46.8             16/46.7  OK
```

## `check_constants_absent.py` — Phase ζ manual probe

Spot-check for the constant-shaped fns (`pi()`, `time()`, `vector(N)`,
no-arg calendar `hour()` / `minute()`) and the presence detectors
(`absent` / `absent_over_time`). Each expr runs as an instant query
against snowmelt and prom; the harness compares series count and value
with kind-specific tolerances:

- **constants** (`pi()`, `vector(N)`): exact match expected.
- **`time()`**: ±2 s tolerance (clock skew between backends).
- **calendar** (`hour()`, `minute()`): ±1 (boundary tolerance — query
  can land either side of an hour/minute rollover).
- **`absent(populated_metric)`**: both sides must return 0 series.
- **`absent(missing_metric)`**: both sides must return exactly 1 row
  with value=1.

```bash
python3 scripts/check_constants_absent.py
```

Output shape:

```
op                                                          snow n/value          prom n/value  verdict
----------------------------------------------------------------------------------------------------------
pi()                                                          1/3.141592653589793    1/3.141592653589793  OK
time()                                                        1/1714782145          1/1714782145          OK
vector(42)                                                    1/42                  1/42                  OK
hour()                                                        1/13                  1/13                  OK
absent(jvm_thread_count) — should be empty                    0/<empty>             0/<empty>             OK
absent(does_not_exist) — synthetic 1                          1/1                   1/1                   OK
absent_over_time(does_not_exist[5m])                          1/1                   1/1                   OK
```

## `check_label_ops.py` — Phase ε manual probe

Spot-check for `label_join` / `label_replace` (which mutate per-series
labels) and `sort*` (which reorder series). Per-fn output shows series
count + the manipulated label value (or first-series sample value for
sort ops).

```bash
python3 scripts/check_label_ops.py
python3 scripts/check_label_ops.py --metric jvm_class_loaded
```

## `check_aggregator_params.py` — Phase δ manual probe

~10 representative aggregator-with-parameter exprs against snowmelt
+ prom: `topk` / `bottomk` (multi-output, label-preserving),
`quantile` and the by/without variant (single-output collapse),
`stddev` / `stdvar` (no extra param), `count_values` (one series
per distinct value).

Multi-output ops are summarised by series count + first-sample
value + Σ-of-values. The Σ comparison gives a single scalar
invariant per panel — works regardless of how many series each
side returned.

```bash
python3 scripts/check_aggregator_params.py
python3 scripts/check_aggregator_params.py --metric jvm_thread_count
```

Output shape:

```
expression                                          snow ser/sample/Σ          prom ser/sample/Σ          match?
--------------------------------------------------------------------------------------------------
topk(5, m)                                              5/3.9e+08/1.4e+09          5/3.9e+08/1.4e+09  OK
bottomk(5, m)                                           5/2.1e+08/8.8e+08          5/2.1e+08/8.8e+08  OK
quantile(0.95, m)                                       1/3.85e+08/3.85e+08        1/3.85e+08/3.85e+08  OK
stddev by (cloud_provider)(m)                           3/4.5e+07/1.4e+08          3/4.5e+07/1.4e+08  OK
count_values("v", jvm_thread_count)                     6/2/12                     6/2/12  OK
```

## `perf_compare.py` — latency benchmark

Runs every dashboard panel's `query_range` N times against each
backend (after a warm-up) and tabulates min / p50 / mean latency,
series count, and the snowmelt-vs-prometheus speedup ratio per
query. Closes with a geomean-of-speedups summary across all 30
queries. Useful for spotting regressions on the snowmelt PromQL
adapter or comparing planner changes.

```bash
# Defaults: now-5m → now, step 15s, 5 iterations.
python3 scripts/perf_compare.py

# Wider window — e.g., 15m or 1h.
python3 scripts/perf_compare.py --window-secs 900
python3 scripts/perf_compare.py --window-secs 3600 --iters 10

# Pin to a fixed Unix-second range so two runs are comparable apples-to-apples.
python3 scripts/perf_compare.py --start 1777890000 --end 1777893600
```

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--window-secs` | `300` | Width of `now-X → now`. Ignored if both `--start` / `--end` are passed. |
| `--start` / `--end` | (unset) | Explicit Unix-second range. Must be passed together. |
| `--iters` | `5` | Iterations per query per backend. |
| `--step` | `15` | `query_range` step (seconds or PromQL duration). |

### Output shape

```
Query                            |  P-mean   P-p50   P-min P-srs |  S-mean   S-p50   S-min S-srs | speedup
----------------------------------------------------------------------------------------------------------
test                             |     3.6     3.6     2.7     1 |    27.0    27.1    25.4     1 |  0.13x P faster
rate(jvm_class_loaded)           |     3.4     3.0     2.7     3 |    14.5    14.2    13.8     3 |  0.23x P faster
…
geomean speedup (S vs P): 0.19x  (>1 means snowmelt faster on average)
```

Columns:
- `P-*` / `S-*` — Prometheus / Snowmelt latency in milliseconds.
- `P-srs` / `S-srs` — series returned (sanity check that the query
  actually hit data on each backend; mismatches usually mean a name
  / suffix issue, not a perf problem).
- `speedup` = `Prom_mean / Snow_mean`. `> 1.0` means snowmelt faster.

## Reference numbers — single-node Helm install on Docker Desktop k8s

Captured against the dashboards in `charts/snowmelt/templates/`,
single-node snowmelt + the in-chart Prometheus, OTel datagen running
on default knobs. Values are wall-clock latency at the HTTP layer
(ms). 5 iterations + 1 warm-up per query per backend.

> **Caveat** — these were captured against the pre-`min_window_us`-fix
> snowmelt binary, where `rate(metric[1m])` queries fetched only
> `[end − 60s, end]` instead of `[start − 60s, end]`. So the
> rate-family rows are artificially fast on the snowmelt side
> (less data fetched, fewer step points emitted). Bare selectors
> and `sum/avg/min/max` aggregates are unaffected by that bug and
> represent the steady-state shape. Re-run after deploying a
> post-fix image for an accurate rate comparison.

### 5-minute window (`now-5m → now`, step 15s)

| Query | P-mean | P-p50 | P-min | P-srs | S-mean | S-p50 | S-min | S-srs | S/P |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `test`                                                   |  3.6 |  3.6 |  2.7 | 1 | 27.0 | 27.1 | 25.4 | 1 | 7.5× |
| `rate(jvm_class_loaded[1m])`                             |  3.4 |  3.0 |  2.7 | 3 | 14.5 | 14.2 | 13.8 | 3 | 4.3× |
| `rate(mysql_query_count[1m])`                            |  4.0 |  3.8 |  3.6 | 3 | 20.7 | 20.4 | 20.0 | 3 | 5.2× |
| `rate(redis_commands_processed[1m])`                     |  3.3 |  3.3 |  2.7 | 1 | 19.0 | 19.1 | 18.1 | 1 | 5.7× |
| `rate(postgresql_commits[1m])`                           |  3.5 |  3.0 |  2.8 | 1 | 18.6 | 18.2 | 18.1 | 1 | 5.3× |
| `rate(mongodb_operation_count[1m])`                      |  3.5 |  3.7 |  2.8 | 1 | 13.3 | 13.2 | 12.8 | 1 | 3.8× |
| `jvm_memory_used*`                                       |  3.4 |  3.1 |  3.0 | 1 | 17.2 | 17.4 | 16.0 | 1 | 5.0× |
| `jvm_thread_count`                                       |  3.6 |  3.8 |  2.9 | 6 | 27.0 | 26.8 | 26.5 | 6 | 7.5× |
| `redis_memory_used*`                                     |  3.8 |  4.2 |  2.9 | 2 | 17.4 | 17.4 | 16.4 | 2 | 4.5× |
| `process_memory_usage*`                                  |  3.4 |  3.3 |  2.7 | 6 | 19.2 | 18.8 | 18.5 | 6 | 5.6× |
| `process_runtime_go_goroutines`                          |  3.7 |  3.5 |  3.1 | 9 | 18.9 | 19.2 | 18.0 | 9 | 5.1× |
| `jvm_cpu_recent_util*`                                   |  3.4 |  3.4 |  3.0 | 6 | 19.1 | 18.9 | 17.6 | 6 | 5.6× |
| `process_cpu_util*`                                      |  3.6 |  3.7 |  2.9 | 6 | 18.0 | 17.9 | 17.5 | 6 | 5.0× |
| `mongodb_health`                                         |  3.6 |  3.6 |  2.8 | 0 | 17.2 | 17.1 | 16.4 | 0 | 4.7× |
| `kafka_consumer_group_lag`                               |  3.8 |  3.1 |  2.7 | 2 | 17.4 | 17.7 | 16.6 | 2 | 4.6× |
| `elasticsearch_cluster_shards`                           |  3.4 |  3.6 |  2.9 | 3 | 18.3 | 18.2 | 17.7 | 3 | 5.4× |
| `jvm_gc_duration` (Prom: `rate(_count[1m])`)             |  3.2 |  3.0 |  2.6 | 0 | 16.7 | 16.8 | 16.2 | 0 | 5.2× |
| `http_server_duration` (Prom: `rate(_count[1m])`)        |  3.2 |  3.2 |  2.7 | 0 | 17.0 | 16.8 | 16.4 | 0 | 5.3× |
| `http_client_duration` (Prom: `rate(_count[1m])`)        |  3.3 |  3.6 |  2.4 | 1/0 | 16.9 | 16.6 | 16.1 | 0 | 5.1× |
| `spring_batch_job_duration` (Prom: `rate(_count[1m])`)   |  3.4 |  3.3 |  2.5 | 0 | 16.8 | 16.7 | 16.6 | 0 | 4.9× |
| `sum by (cp) (jvm_memory_used*)`                         |  3.1 |  2.9 |  2.6 | 1 | 18.0 | 18.1 | 16.9 | 1 | 5.8× |
| `sum by (ns) (rate(mysql_query_count*[1m]))`             |  3.6 |  3.7 |  2.9 | 3 | 18.7 | 19.0 | 17.9 | 3 | 5.2× |
| `avg by (lang) (process_cpu_utilization*)`               |  3.4 |  3.2 |  2.9 | 4 | 18.5 | 18.5 | 17.5 | 4 | 5.4× |
| `max by (cp) (jvm_thread_count)`                         |  4.5 |  4.6 |  3.2 | 3 | 27.2 | 26.5 | 26.0 | 3 | 6.0× |
| `min by (cp) (redis_memory_used*)`                       |  3.7 |  3.7 |  3.0 | 1 | 18.1 | 17.7 | 17.2 | 1 | 4.9× |
| `count by (cp) (jvm_class_loaded*)`                      |  3.3 |  3.5 |  2.7 | 2 | 18.2 | 18.1 | 17.6 | 2 | 5.5× |
| `sum without (pod) (postgresql_commits*)`                |  3.8 |  4.0 |  3.4 | 1 | 26.1 | 26.0 | 25.3 | 1 | 6.9× |
| `sum(rate(http_server_duration*[1m]))`                   |  2.9 |  2.8 |  2.5 | 0 | 13.9 | 13.9 | 13.2 | 0 | 4.8× |
| `avg by (svc) (rate(redis_commands_processed*[1m]))`     |  3.4 |  3.7 |  2.8 | 1 | 18.7 | 18.4 | 18.3 | 1 | 5.5× |
| `group by (cp) (kafka_consumer_group_lag)`               |  3.2 |  2.9 |  2.8 | 2 | 17.6 | 17.2 | 16.7 | 2 | 5.5× |

**Geomean S/P = 0.19× → ~5.3× slower. Prometheus wins 30/30.**

### 15-minute window (`now-15m → now`, step 15s)

| Query | P-mean | P-p50 | P-min | P-srs | S-mean | S-p50 | S-min | S-srs | S/P |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `test`                                                   |  3.8 |  4.1 |  2.4 | 1 | 26.2 | 25.2 | 23.1 | 1 | 6.9× |
| `rate(jvm_class_loaded[1m])`                             |  4.1 |  3.8 |  3.0 | 3 | 28.4 | 22.5 | 18.4 | 3 | 6.9× |
| `rate(mysql_query_count[1m])`                            |  3.6 |  3.4 |  2.8 | 3 | 26.9 | 26.3 | 26.0 | 3 | 7.5× |
| `rate(redis_commands_processed[1m])`                     |  3.2 |  3.4 |  2.7 | 1 | 24.3 | 24.0 | 23.5 | 1 | 7.6× |
| `rate(postgresql_commits[1m])`                           |  3.1 |  3.0 |  2.5 | 1 | 24.3 | 24.7 | 23.2 | 1 | 7.8× |
| `rate(mongodb_operation_count[1m])`                      |  3.7 |  3.4 |  2.7 | 1 | 17.5 | 17.4 | 16.5 | 1 | 4.7× |
| `jvm_memory_used*`                                       |  3.6 |  3.7 |  2.7 | 1 | 16.9 | 17.0 | 16.1 | 1 | 4.7× |
| `jvm_thread_count`                                       |  4.6 |  4.6 |  3.7 | 6 | 26.1 | 26.4 | 25.4 | 6 | 5.7× |
| `redis_memory_used*`                                     |  4.3 |  3.9 |  3.1 | 2 | 16.9 | 16.6 | 16.3 | 2 | 4.0× |
| `process_memory_usage*`                                  |  4.6 |  4.5 |  3.3 | 6 | 18.9 | 18.9 | 18.2 | 6 | 4.1× |
| `process_runtime_go_goroutines`                          |  4.0 |  3.7 |  3.4 | 9 | 19.2 | 19.3 | 18.8 | 9 | 4.8× |
| `jvm_cpu_recent_util*`                                   |  3.6 |  3.5 |  3.0 | 6 | 18.6 | 18.6 | 18.4 | 6 | 5.2× |
| `process_cpu_util*`                                      |  4.2 |  4.3 |  3.2 | 6 | 19.2 | 18.9 | 17.4 | 6 | 4.6× |
| `mongodb_health`                                         |  3.5 |  3.6 |  2.6 | 0 | 17.0 | 16.5 | 16.0 | 0 | 4.9× |
| `kafka_consumer_group_lag`                               |  3.3 |  3.2 |  3.1 | 2 | 18.2 | 17.6 | 16.7 | 2 | 5.5× |
| `elasticsearch_cluster_shards`                           |  3.7 |  3.8 |  3.3 | 3 | 18.1 | 18.0 | 17.2 | 3 | 4.9× |
| `jvm_gc_duration` (Prom: `rate(_count[1m])`)             |  3.7 |  3.7 |  2.6 | 0 | 16.4 | 16.6 | 15.3 | 0 | 4.4× |
| `http_server_duration` (Prom: `rate(_count[1m])`)        |  3.4 |  3.3 |  3.0 | 0 | 16.7 | 16.8 | 16.0 | 0 | 4.9× |
| `http_client_duration` (Prom: `rate(_count[1m])`)        |  3.9 |  3.7 |  3.6 | 1/0 | 16.5 | 16.4 | 16.2 | 0 | 4.2× |
| `spring_batch_job_duration` (Prom: `rate(_count[1m])`)   |  3.7 |  3.7 |  2.9 | 0 | 16.3 | 16.1 | 15.4 | 0 | 4.4× |
| `sum by (cp) (jvm_memory_used*)`                         |  4.0 |  4.1 |  2.8 | 1 | 18.7 | 18.4 | 16.9 | 1 | 4.7× |
| `sum by (ns) (rate(mysql_query_count*[1m]))`             |  3.8 |  3.1 |  2.9 | 3 | 26.7 | 26.4 | 25.9 | 3 | 7.0× |
| `avg by (lang) (process_cpu_utilization*)`               |  4.1 |  4.4 |  3.0 | 4 | 20.1 | 19.6 | 18.1 | 4 | 4.9× |
| `max by (cp) (jvm_thread_count)`                         |  3.9 |  4.0 |  2.7 | 3 | 25.9 | 26.2 | 24.9 | 3 | 6.6× |
| `min by (cp) (redis_memory_used*)`                       |  3.4 |  3.4 |  2.9 | 1 | 17.1 | 16.9 | 16.6 | 1 | 5.0× |
| `count by (cp) (jvm_class_loaded*)`                      |  3.5 |  3.4 |  2.8 | 2 | 18.2 | 18.3 | 17.5 | 2 | 5.2× |
| `sum without (pod) (postgresql_commits*)`                |  3.4 |  3.5 |  2.4 | 1 | 24.6 | 24.5 | 24.2 | 1 | 7.3× |
| `sum(rate(http_server_duration*[1m]))`                   |  3.5 |  3.2 |  2.6 | 0 | 16.8 | 16.9 | 15.5 | 0 | 4.8× |
| `avg by (svc) (rate(redis_commands_processed*[1m]))`     |  4.2 |  4.0 |  3.8 | 1 | 24.7 | 24.5 | 24.0 | 1 | 5.9× |
| `group by (cp) (kafka_consumer_group_lag)`               |  3.9 |  3.8 |  3.6 | 2 | 18.0 | 18.2 | 16.9 | 2 | 4.6× |

**Geomean S/P = 0.19× → ~5.3× slower. Prometheus wins 30/30.**

### 1-hour window — backfilled (`now-2h → now-1h`, captured 2026-05-04T15:06Z)

Range pinned to a stable historical hour (start `1777903582`, end
`1777907182` Unix sec) so the data was fully ingested + flushed
before the benchmark hit it — no in-flight WAL contention, no
near-`now` ingestion overhead. Same 30 queries, step 15s, 5 iters.

| Query | P-mean | P-p50 | P-min | P-srs | S-mean | S-p50 | S-min | S-srs | S/P |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `test`                                                   |  3.6 |  3.5 |  2.9 | 1 | 15.9 | 15.1 | 14.2 | 1 | 4.4× |
| `rate(jvm_class_loaded[1m])`                             |  4.4 |  3.8 |  3.2 | 3 | 21.1 | 20.6 | 18.7 | 3 | 4.8× |
| `rate(mysql_query_count[1m])`                            |  4.5 |  4.4 |  3.4 | 3 | 20.3 | 20.4 | 18.0 | 3 | 4.5× |
| `rate(redis_commands_processed[1m])`                     |  3.8 |  3.7 |  3.1 | 1 | 17.6 | 17.4 | 14.7 | 1 | 4.6× |
| `rate(postgresql_commits[1m])`                           |  3.6 |  3.8 |  3.1 | 1 | 15.0 | 14.7 | 13.8 | 1 | 4.2× |
| `rate(mongodb_operation_count[1m])`                      |  3.6 |  3.5 |  2.7 | 1 | 16.3 | 15.1 | 14.9 | 1 | 4.6× |
| `jvm_memory_used*`                                       |  4.1 |  4.1 |  3.2 | 1 | 13.9 | 13.2 | 13.1 | 1 | 3.4× |
| `jvm_thread_count`                                       |  4.7 |  4.3 |  3.7 | 6 | 21.8 | 22.0 | 18.9 | 6 | 4.7× |
| `redis_memory_used*`                                     |  4.1 |  4.1 |  3.3 | 2 | 15.5 | 15.0 | 14.3 | 2 | 3.8× |
| `process_memory_usage*`                                  |  4.7 |  4.7 |  4.0 | 6 | 23.3 | 25.8 | 18.5 | 6 | 5.0× |
| `process_runtime_go_goroutines`                          |  5.0 |  4.6 |  4.2 | 9 | **66.4** | **63.5** | 24.9 | 9 | **13.3×** |
| `jvm_cpu_recent_util*`                                   |  5.8 |  5.9 |  4.9 | 6 | **47.4** | **33.6** | 21.0 | 6 | **8.2×** |
| `process_cpu_util*`                                      |  4.5 |  4.7 |  3.7 | 6 | 20.4 | 18.6 | 17.7 | 6 | 4.5× |
| `mongodb_health`                                         |  3.7 |  3.3 |  3.2 | 0 | 12.7 | 12.9 | 11.9 | 0 | 3.4× |
| `kafka_consumer_group_lag`                               |  4.8 |  4.5 |  3.7 | 2 | 15.5 | 14.6 | 14.3 | 2 | 3.2× |
| `elasticsearch_cluster_shards`                           |  4.9 |  4.7 |  4.0 | 3 | 20.2 | 18.9 | 16.1 | 3 | 4.1× |
| `jvm_gc_duration` (Prom: `rate(_count[1m])`)             |  3.6 |  3.5 |  2.7 | 0 | 13.4 | 13.6 | 12.3 | 0 | 3.7× |
| `http_server_duration` (Prom: `rate(_count[1m])`)        |  3.1 |  3.4 |  2.5 | 0 | 12.3 | 11.8 | 11.2 | 0 | 4.0× |
| `http_client_duration` (Prom: `rate(_count[1m])`)        |  3.6 |  3.6 |  2.9 | 1/0 | 12.4 | 12.6 | 11.3 | 0 | 3.4× |
| `spring_batch_job_duration` (Prom: `rate(_count[1m])`)   |  3.9 |  3.7 |  3.0 | 0 | 12.7 | 12.1 | 11.8 | 0 | 3.2× |
| `sum by (cp) (jvm_memory_used*)`                         |  3.7 |  3.8 |  3.0 | 1 | 15.3 | 15.1 | 14.7 | 1 | 4.1× |
| `sum by (ns) (rate(mysql_query_count*[1m]))`             |  4.1 |  4.0 |  3.3 | 3 | 20.6 | 19.9 | 19.3 | 3 | 5.0× |
| `avg by (lang) (process_cpu_utilization*)`               |  4.3 |  4.4 |  3.9 | 4 | 21.1 | 21.1 | 20.3 | 4 | 4.9× |
| `max by (cp) (jvm_thread_count)`                         |  4.2 |  4.4 |  3.4 | 3 | 22.5 | 22.0 | 20.7 | 3 | 5.4× |
| `min by (cp) (redis_memory_used*)`                       |  4.1 |  4.1 |  3.7 | 1 | 16.0 | 15.5 | 15.1 | 1 | 3.9× |
| `count by (cp) (jvm_class_loaded*)`                      |  3.6 |  3.3 |  2.9 | 2 | 16.4 | 16.6 | 15.4 | 2 | 4.6× |
| `sum without (pod) (postgresql_commits*)`                |  3.9 |  4.2 |  2.7 | 1 | 23.6 | 17.5 | 14.8 | 1 | 6.1× |
| `sum(rate(http_server_duration*[1m]))`                   |  3.2 |  3.1 |  2.6 | 0 | 12.9 | 13.1 | 11.3 | 0 | 4.0× |
| `avg by (svc) (rate(redis_commands_processed*[1m]))`     |  3.8 |  4.1 |  2.8 | 1 | 21.1 | 16.7 | 15.0 | 1 | 5.5× |
| `group by (cp) (kafka_consumer_group_lag)`               |  3.6 |  3.2 |  2.7 | 2 | 16.3 | 16.1 | 15.7 | 2 | 4.5× |

**Geomean S/P = 0.22× → ~4.5× slower. Prometheus wins 30/30.**

Two outliers stand out — both query high-cardinality gauges:
- **`process_runtime_go_goroutines` (9 series): mean 66.4 ms vs min 24.9 ms** — wide tail. Likely DataFusion JOIN re-plan / Vortex file open variance.
- **`jvm_cpu_recent_utilization` (6 series): mean 47.4 ms vs min 21.0 ms** — similar pattern.

Excluding those two, geomean S/P drops to ~0.25× (4.0× slower) on the 1h window.

### Roll-up across windows

|                                | 5m mean | 15m mean | 1h mean (backfilled) | Δ vs 15m |
|---|---:|---:|---:|---:|
| Prometheus (across 30 queries) |  3.5 ms |  3.7 ms | 4.1 ms | +0.4 ms |
| Snowmelt (across 30 queries)   | 18.6 ms | 20.4 ms | 19.7 ms | −0.7 ms |
| Geomean S/P                    | 0.19×   | 0.19×   | 0.22×   | better  |

- **Prometheus is essentially window-insensitive** — 5m vs 15m vs
  1h-backfilled moved its mean from 3.5 → 4.1 ms (+17% across a 12×
  range). In-memory TSDB; the chunk walk is O(window) but tiny in
  absolute terms.
- **Snowmelt scales mildly with window width** for non-rate queries
  (SQL fetches 3–12× more samples; latency moves 0.5–2 ms).
  Rate-family queries widen the most when the window grows
  (`rate(jvm_class_loaded)` 14.5 → 28.4 → 21.1 ms across 5m/15m/1h)
  because the per-step bucketer iterates more step points. Post-fix
  (with `min_window_us` honoured) expect rate latencies to grow
  further as the SQL fetch widens to cover `[start − range, end]`
  instead of just `[end − range, end]`.
- **The 1h-backfilled window is slightly *faster* than 15m** — small
  but real (geomean S/P 0.22× vs 0.19×). Two factors:
  - The benchmark doesn't race against real-time WAL flushes — the
    hour `now-2h → now-1h` is fully settled, so file scans hit
    closed Vortex files only.
  - The min latencies show typical run-to-run variance (machine
    load) is on the order of a couple ms, which is the order of the
    delta itself.

### Where the gap lives

A 15–27 ms snowmelt query over a few hundred samples is dominated by
fixed overheads, not data work:
1. Flight SQL gRPC round-trip from the HTTP/PromQL adapter to the
   in-process Flight server (loopback, but still serialise +
   deserialise).
2. DataFusion plan + JOIN of `vortex_table` with the per-partition
   metadata `MemTable`.
3. Per-step bucketing in Rust (~20–60 step points; cheap).

The 3 ms Prometheus baseline includes none of this — it's a single
in-memory series walk.

The biggest S/P gaps (≥7×) are all **rate / sum-of-rate** queries —
that's where the optimization headroom is, especially after the
`min_window_us` fix lands and rate fetches widen to the full
`[start − range, end]` band.
