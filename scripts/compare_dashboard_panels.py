#!/usr/bin/env python3
"""Compare every panel query in app-stack Grafana dashboards
between Snowmelt (PromQL HTTP @ :9092) and Prometheus (@ :9090).

Reads `charts/snowmelt/files/<name>-snowmelt.json`, walks rows →
panels → targets, extracts each `expr`, resolves $var template
variables against Snowmelt, runs the SAME expression on both
backends, and tabulates the diff.

Histogram queries (`histogram_quantile`, `_bucket{...}`,
`_bucket[...]`) are skipped — the OTLP datagen filters them
client-side, so neither backend has the bucket structure.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SNOW_BASE = "http://localhost:9092/promql/api/v1"
PROM_BASE = "http://localhost:9090/api/v1"

DASHBOARDS = [
    "django",
    "go",
    "jvm",
    "k8s",
    "langchain",
    "minio",
    "mongodb",
    "nodeexp",
    "nodejs",
]

# Tolerances. Anything past 5% counts as a real mismatch worth
# inspecting; <0.5% is "noise floor" (rate boundary extrapolation,
# step-grid alignment jitter).
NOISE_FLOOR = 0.005
WARN_THRESHOLD = 0.05


def is_histogram_query(expr: str) -> bool:
    # Track 3 lands `histogram_quantile()`; Track 1+2 lands the
    # bucket / sum / count series. So nothing histogram-related
    # needs skipping any more — every shape (Pattern A/B/C) flows
    # through the harness now.
    return False


def http_get_json(url: str, timeout: int = 30) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        try:
            return json.loads(body)
        except Exception:
            return {
                "status": "error",
                "errorType": "http",
                "error": f"HTTP {e.code}: {body[:200]}",
            }
    except Exception as exc:
        return {"status": "error", "errorType": "transport", "error": str(exc)[:200]}


def http_query_range(base: str, expr: str, start: int, end: int, step: int) -> dict:
    params = urllib.parse.urlencode(
        {
            "query": expr,
            "start": str(start),
            "end": str(end),
            "step": str(step),
        }
    )
    url = f"{base}/query_range?{params}"
    return http_get_json(url)


def http_label_values(base: str, label: str, match: str | None = None) -> list[str]:
    params: list[tuple[str, str]] = []
    if match:
        params.append(("match[]", match))
    qs = urllib.parse.urlencode(params)
    url = f"{base}/label/{label}/values" + (f"?{qs}" if qs else "")
    r = http_get_json(url, timeout=10)
    if r.get("status") == "success":
        return r.get("data", []) or []
    return []


# ── Template variable resolution ────────────────────────────────────


def label_values_via_query(base: str, label: str, selector: str | None) -> list[str]:
    """Resolve `label_values(<selector>?, <label>)`.

    For selectorless queries we use `/label/<l>/values` directly
    (Snowmelt rejects `count by(...) ({__name__!=\"\"})` because it
    requires an explicit metric name in selectors). For selector
    queries we run `count by(label) (selector)` because Snowmelt's
    `/label/.../values?match[]=...` ignores the match[] filter (a
    bug we want to surface separately, not work around silently).
    """
    if not selector:
        return http_label_values(base, label)
    count_expr = f"count by({label}) ({selector})"
    url = f"{base}/query?{urllib.parse.urlencode({'query': count_expr})}"
    r = http_get_json(url, timeout=15)
    if r.get("status") != "success":
        return []
    out: list[str] = []
    for s in (r.get("data") or {}).get("result", []) or []:
        v = (s.get("metric") or {}).get(label)
        if v:
            out.append(v)
    out.sort()
    return out


def resolve_templating(dashboard: dict, base: str) -> dict[str, str]:
    """Pick a concrete value for every `$var` in the dashboard.

    Snowmelt's `/label/<l>/values?match[]=...` currently ignores the
    `match[]` filter (TODO: fix), which would mis-resolve dependent
    vars like `$instance = label_values(metric{app=\"$app\"}, instance)`.
    We work around it by running the selector as a `query` and
    pulling labels off the result. Earlier-resolved vars are
    substituted into later selectors before evaluation.
    """
    resolved: dict[str, str] = {}
    for v in dashboard.get("templating", {}).get("list", []) or []:
        name = v.get("name")
        if not name or v.get("type") == "datasource":
            continue
        q = v.get("query") or v.get("definition") or ""
        if isinstance(q, dict):
            q = q.get("query", "")
        # `label_values(<selector>, <label>)` form.
        m = re.match(r"\s*label_values\((.+),\s*(\w+)\s*\)\s*$", q)
        if m:
            sel, label = m.group(1).strip(), m.group(2).strip()
            for prev_n, prev_v in resolved.items():
                sel = sel.replace(f"${prev_n}", prev_v).replace(
                    "${" + prev_n + "}", prev_v
                )
            values = label_values_via_query(base, label, sel)
            resolved[name] = values[0] if values else ""
            continue
        # `label_values(<label>)` — bare label, no selector.
        m = re.match(r"\s*label_values\(\s*(\w+)\s*\)\s*$", q)
        if m:
            label = m.group(1).strip()
            values = label_values_via_query(base, label, None)
            resolved[name] = values[0] if values else ""
            continue
        # Custom / unknown — fall back to first option if provided.
        opts = v.get("options") or []
        if opts:
            first = opts[0]
            if isinstance(first, dict):
                resolved[name] = first.get("value") or first.get("text") or ""
            else:
                resolved[name] = str(first)
        else:
            resolved[name] = ""
    return resolved


def substitute(expr: str, vars_: dict[str, str]) -> str:
    out = expr
    # Longer names first so $instance doesn't eat $instance_id.
    for k in sorted(vars_, key=len, reverse=True):
        v = vars_[k]
        out = out.replace("${" + k + "}", v).replace(f"${k}", v)
    return out


def has_unresolved_var(expr: str) -> bool:
    # `$__interval`, `$__range`, etc. are Grafana built-ins; mark as
    # unresolved so we skip rather than silently sending a `$` to the
    # backend.
    return bool(re.search(r"\$\w+|\$\{\w+\}", expr))


# ── Panel walk ──────────────────────────────────────────────────────


def extract_panel_queries(dashboard: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def visit_panel(p: dict):
        title = p.get("title") or "?"
        for t in p.get("targets", []) or []:
            expr = t.get("expr")
            if expr:
                out.append((title, expr))
        for sub in p.get("panels", []) or []:
            visit_panel(sub)

    # Old-style `rows` -> `panels` -> `targets`.
    for row in dashboard.get("rows", []) or []:
        for panel in row.get("panels", []) or []:
            visit_panel(panel)
    # Newer-style top-level `panels`.
    for panel in dashboard.get("panels", []) or []:
        visit_panel(panel)
    return out


# ── Compare ──────────────────────────────────────────────────────────


def safe_float(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return float("nan")


def fingerprint(series: dict) -> tuple:
    m = {k: v for k, v in (series.get("metric") or {}).items() if k != "__name__"}
    return tuple(sorted(m.items()))


def last_value(series: dict) -> float:
    if "values" in series:
        vs = series["values"]
        if not vs:
            return float("nan")
        return safe_float(vs[-1][1])
    return safe_float(series.get("value", [None, ""])[1])


def compare_results(snow: dict, prom: dict) -> tuple[str, str]:
    if snow.get("status") != "success":
        return "snow_err", (snow.get("error") or "")[:140]
    if prom.get("status") != "success":
        return "prom_err", (prom.get("error") or "")[:140]
    snow_series = (snow.get("data") or {}).get("result", []) or []
    prom_series = (prom.get("data") or {}).get("result", []) or []
    if not snow_series and not prom_series:
        return "both_empty", "no series on either side"
    if not snow_series:
        return "snow_empty", f"snow=0, prom={len(prom_series)}"
    if not prom_series:
        return "prom_empty", f"snow={len(snow_series)}, prom=0"

    snow_by = {fingerprint(s): s for s in snow_series}
    prom_by = {fingerprint(s): s for s in prom_series}
    common = set(snow_by) & set(prom_by)
    if not common:
        return (
            "no_label_overlap",
            f"snow={len(snow_series)}, prom={len(prom_series)}, common=0",
        )

    rel_errors: list[float] = []
    nan_count = 0
    for k in common:
        sv = last_value(snow_by[k])
        pv = last_value(prom_by[k])
        if sv != sv or pv != pv:  # NaN
            nan_count += 1
            continue
        denom = max(abs(pv), abs(sv), 1e-12)
        rel_errors.append(abs(sv - pv) / denom)

    if not rel_errors:
        return "all_nan", f"common={len(common)}, all values NaN"

    rel_errors.sort()
    median_err = rel_errors[len(rel_errors) // 2]
    max_err = rel_errors[-1]
    series_off = abs(len(snow_series) - len(prom_series))

    if max_err < NOISE_FLOOR and series_off == 0:
        verdict = "ok"
    elif max_err < WARN_THRESHOLD:
        verdict = "ok_noise"
    else:
        verdict = "MISMATCH"
    detail = (
        f"snow={len(snow_series)}, prom={len(prom_series)}, common={len(common)}, "
        f"med_err={median_err:.2%}, max_err={max_err:.2%}"
    )
    if nan_count:
        detail += f", nan={nan_count}"
    return verdict, detail


# ── Main ─────────────────────────────────────────────────────────────


def main():
    end = int(time.time())
    start = end - 5 * 60
    step = 30

    base = Path(__file__).resolve().parent.parent / "charts" / "snowmelt" / "files"

    rows: list[tuple[str, str, str, str, str, str]] = []
    # rows: (dashboard, panel, expr_after_substitution_truncated, raw_expr, verdict, detail)

    for name in DASHBOARDS:
        path = base / f"{name}-snowmelt.json"
        if not path.exists():
            print(f"# missing dashboard: {path}")
            continue
        with path.open() as f:
            dash = json.load(f)
        vars_ = resolve_templating(dash, SNOW_BASE)
        queries = extract_panel_queries(dash)
        for ptitle, raw_expr in queries:
            expr = substitute(raw_expr, vars_)
            if is_histogram_query(expr):
                rows.append((name, ptitle, "(histogram)", raw_expr, "skip_hist", ""))
                continue
            if has_unresolved_var(expr):
                # Often $__interval, $__range — Grafana built-ins. Skip cleanly.
                missing = ",".join(set(re.findall(r"\$\w+", expr)))
                rows.append(
                    (name, ptitle, expr[:60], raw_expr, "skip_var", f"unresolved={missing}")
                )
                continue
            snow = http_query_range(SNOW_BASE, expr, start, end, step)
            prom = http_query_range(PROM_BASE, expr, start, end, step)
            verdict, detail = compare_results(snow, prom)
            rows.append((name, ptitle, expr[:60], raw_expr, verdict, detail))

    # Print table
    width_dash = max(9, max((len(r[0]) for r in rows), default=9))
    width_panel = min(
        38, max(5, max((len(r[1]) for r in rows), default=5))
    )
    width_expr = 60
    print(
        f"{'DASH':{width_dash}s}  {'PANEL':{width_panel}s}  "
        f"{'EXPR (substituted, truncated)':{width_expr}s}  {'VERDICT':12s}  DETAIL"
    )
    print("-" * (width_dash + width_panel + width_expr + 80))
    for d, p, e, _raw, v, det in rows:
        p_short = (p[: width_panel - 1] + "…") if len(p) > width_panel else p
        e_short = (e[: width_expr - 1] + "…") if len(e) > width_expr else e
        print(
            f"{d:{width_dash}s}  {p_short:{width_panel}s}  {e_short:{width_expr}s}  "
            f"{v:12s}  {det}"
        )

    counts: dict[str, int] = {}
    for r in rows:
        counts[r[4]] = counts.get(r[4], 0) + 1

    print()
    print("Summary by verdict:")
    order = [
        "ok",
        "ok_noise",
        "MISMATCH",
        "snow_empty",
        "prom_empty",
        "both_empty",
        "no_label_overlap",
        "all_nan",
        "snow_err",
        "prom_err",
        "skip_hist",
        "skip_var",
    ]
    for v in order:
        if v in counts:
            print(f"  {v:18s} {counts[v]}")
    for v in counts:
        if v not in order:
            print(f"  {v:18s} {counts[v]}")
    total_compared = sum(
        counts.get(v, 0) for v in ["ok", "ok_noise", "MISMATCH"]
    )
    print(f"  {'(compared total)':18s} {total_compared}")


if __name__ == "__main__":
    main()
