#!/usr/bin/env python3
"""
Master harness — runs every Phase α…η check probe in sequence
against snowmelt and prom on localhost (assumes port-forwards
already established) and emits a single consolidated tabular report.

Phases covered:
  α  *_over_time family            (check_over_time.py)
  β  per-sample math/trig/calendar (check_scalar_fns.py)
  γ  counter-aware extras          (check_counter_extras.py)
  δ  aggregator parameters         (check_aggregator_params.py)
  ε  label ops + sort              (check_label_ops.py)
  ζ  constants + presence          (check_constants_absent.py)
  η  predictive + parameterized    (check_predictive.py)
  θ  info()                        — blocked, no probe (see translations.md §15)

Usage:
  python3 scripts/run_all_phase_checks.py [--metric jvm_thread_count]

Stdlib-only.
"""
import argparse
import datetime
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PHASES = [
    ("α", "*_over_time family",            "check_over_time.py"),
    ("β", "math / trig / calendar",         "check_scalar_fns.py"),
    ("γ", "counter-aware extras",           "check_counter_extras.py"),
    ("δ", "aggregator parameters",          "check_aggregator_params.py"),
    ("ε", "label ops + sort",               "check_label_ops.py"),
    ("ζ", "constants + presence",           "check_constants_absent.py"),
    ("η", "predictive + parameterized",     "check_predictive.py"),
]


def run_one(script, extra_args):
    path = os.path.join(SCRIPT_DIR, script)
    cmd = ["python3", path] + extra_args
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return out.returncode, out.stdout, out.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"TIMEOUT after 120s running {script}"


def parse_verdicts(stdout):
    """Walk a probe's table output, return (n_ok, n_diff, list_of_DIFF_rows)."""
    ok = 0
    diff = 0
    diffs = []
    for line in stdout.splitlines():
        if line.endswith(" OK"):
            ok += 1
        elif line.endswith(" DIFF"):
            diff += 1
            diffs.append(line)
        elif "match?" in line and ("OK" in line or "DIFF" in line):
            # alt format: "...   OK" with trailing spaces
            if line.rstrip().endswith("OK"):
                ok += 1
            elif line.rstrip().endswith("DIFF"):
                diff += 1
                diffs.append(line.rstrip())
    return ok, diff, diffs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metric", default=None,
                    help="optional --metric override (passed to scripts that accept it)")
    args = ap.parse_args()

    extra = []
    if args.metric:
        extra = ["--metric", args.metric]

    print(f"# Snowmelt vs Prometheus — full-phase comparison report")
    print(f"# Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    print()

    summary_rows = []
    full_outputs = []

    for tag, label, script in PHASES:
        # Some scripts don't take --metric; try with it first, fall back without.
        rc, out, err = run_one(script, extra)
        if rc != 0 and extra:
            rc, out, err = run_one(script, [])
        ok, diff, diffs = parse_verdicts(out)
        status = "OK" if rc == 0 and diff == 0 else (
            "FAIL (rc!=0)" if rc != 0 else "DIFF"
        )
        summary_rows.append((tag, label, script, ok, diff, status))
        full_outputs.append((tag, label, script, rc, out, err, diffs))

    # --- Top-line summary table ---
    print("## Summary")
    print()
    print(f"{'Phase':<6} {'Description':<32} {'Probe script':<32} {'OK':>4} {'DIFF':>5}  Status")
    print("-" * 96)
    for tag, label, script, ok, diff, status in summary_rows:
        print(f"  {tag:<4} {label:<32} {script:<32} {ok:>4} {diff:>5}  {status}")
    # Phase θ — blocked, no probe.
    print(f"  {'θ':<4} {'info()':<32} {'(no probe)':<32} {'':>4} {'':>5}  BLOCKED — see translations.md §15")
    print()

    # --- Per-phase detailed output ---
    for tag, label, script, rc, out, err, diffs in full_outputs:
        print()
        print(f"## Phase {tag} — {label}  ({script})")
        if rc != 0:
            print(f"### Exit code: {rc}")
            if err:
                print("### stderr:")
                for line in err.splitlines()[:20]:
                    print(f"    {line}")
        print()
        print("```")
        print(out.rstrip())
        print("```")
        if diffs:
            print()
            print(f"### {len(diffs)} divergence(s):")
            for d in diffs:
                print(f"- `{d.strip()}`")


if __name__ == "__main__":
    sys.exit(main())
