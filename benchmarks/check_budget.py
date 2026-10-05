"""Score benchmark reports against the adapter's overhead budget.

    python benchmarks/check_budget.py docs/benchmark-results/*.json [--min-runs 20]

The budget (rationale in docs/benchmarks.md) applies to reports written by
``benchmark_evidence.py``. A report with fewer than ``--min-runs`` runs is scored
but marked INDICATIVE: tens of milliseconds of run-to-run noise on a laptop make
single 10-run medians too jittery to gate on. Exit code is non-zero if any
sufficiently large report breaks the budget.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Each limit is "whichever is larger" of an absolute floor and a relative share of
# the direct (no-adapter) figure, so tiny/fast models are not held to sub-ms limits.
TTFT_OVERHEAD_MS = 25.0
TTFT_OVERHEAD_SHARE = 0.25
TOTAL_OVERHEAD_MS = 50.0
TOTAL_OVERHEAD_SHARE = 0.05
MIN_THROUGHPUT_RATIO = 0.90
MAX_RSS_MB = 100.0
MAX_IDLE_CPU_PERCENT = 2.0


def evaluate(report: dict) -> list[tuple[str, bool, str]]:
    checks = []
    ttft_d, ttft_a = (report["ttft_ms"][k]["median"] for k in ("direct", "a2a"))
    limit = max(TTFT_OVERHEAD_MS, TTFT_OVERHEAD_SHARE * ttft_d)
    checks.append(
        (
            "TTFT overhead",
            ttft_a - ttft_d <= limit,
            f"{ttft_a - ttft_d:+.1f} ms (limit {limit:.0f})",
        )
    )

    tot_d, tot_a = (report["total_ms"][k]["median"] for k in ("direct", "a2a"))
    limit = max(TOTAL_OVERHEAD_MS, TOTAL_OVERHEAD_SHARE * tot_d)
    checks.append(
        (
            "Total latency overhead",
            tot_a - tot_d <= limit,
            f"{tot_a - tot_d:+.1f} ms (limit {limit:.0f})",
        )
    )

    rate_d, rate_a = (report["chunks_per_s"][k]["median"] for k in ("direct", "a2a"))
    ratio = rate_a / rate_d if rate_d else 1.0
    checks.append(
        (
            "Streaming throughput",
            ratio >= MIN_THROUGHPUT_RATIO,
            f"{ratio:.0%} of direct (min {MIN_THROUGHPUT_RATIO:.0%})",
        )
    )

    checks.append(
        (
            "Cancellation",
            report["cancellation"]["result"] == "pass",
            report["cancellation"]["result"],
        )
    )

    footprint = report.get("adapter_footprint") or {}
    if (
        "rss_mb" in footprint and footprint["rss_mb"] > 20
    ):  # tiny values mean the wrong PID was sampled
        checks.append(
            (
                "Adapter RSS",
                footprint["rss_mb"] <= MAX_RSS_MB,
                f"{footprint['rss_mb']:.0f} MB (max {MAX_RSS_MB:.0f})",
            )
        )
        idle = footprint["cpu_percent_idle"]
        checks.append(
            (
                "Adapter idle CPU",
                idle <= MAX_IDLE_CPU_PERCENT,
                f"{idle:.1f}% (max {MAX_IDLE_CPU_PERCENT:.0f})",
            )
        )

    for level, targets in (report.get("load") or {}).items():
        direct, a2a = targets["direct"], targets["a2a"]
        ok = a2a["succeeded"] >= direct["succeeded"]
        checks.append(
            (
                f"Load N={level} completions",
                ok,
                f"{a2a['succeeded']}/{a2a['requests']} vs direct {direct['succeeded']}/{direct['requests']}",
            )
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--min-runs", type=int, default=20)
    args = parser.parse_args()

    broken = 0
    for path in args.reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        if "ttft_ms" not in report:
            continue  # not a benchmark_evidence report
        runs = report.get("runs", 0)
        gating = runs >= args.min_runs
        checks = evaluate(report)
        failed = [name for name, ok, _ in checks if not ok]
        status = "PASS" if not failed else ("FAIL" if gating else "OVER (indicative)")
        print(f"{status:<17} {path.name}  [{runs} runs{'' if gating else ', below --min-runs'}]")
        for name, ok, detail in checks:
            if not ok:
                print(f"      x {name}: {detail}")
        broken += bool(failed) and gating
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
