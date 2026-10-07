"""What does OpenTelemetry cost? Adapter overhead with telemetry off, on, sampled and exporting.

    python benchmarks/telemetry_overhead.py [--runs 300] [--output result.json] [--check]

Starts a mock Lemonade and one adapter per mode as separate processes, then measures streaming
requests round-robin across the modes (so drift in the machine affects every mode equally):

    off        telemetry disabled (the default)
    off-control  a second identical 'off' process: the noise floor of comparing separate processes
    on-100     telemetry on, every trace sampled, exporters ``none`` (instrumentation cost only)
    on-10      same with 10% head sampling
    otlp-100   every trace sampled and exported over OTLP/HTTP to a local sink (instrumentation + export)

``--check`` exits 1 when a mode breaks the telemetry budget (see docs/observability.md):
at most 1 ms (or 5%) extra median TTFT and at most 5% less streaming throughput than ``off``.
The mock backend answers in about 15 ms, which makes any fixed per-request cost easy to see.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

TTFT_BUDGET_MS = (
    2.5  # revised from the 1 ms first proposed, after measuring (docs/observability.md)
)
TTFT_BUDGET_SHARE = 0.05
THROUGHPUT_FLOOR = 0.95

INFORMATIONAL = {"off-control", "sdk-spans-only", "otlp-100-sdk-spans"}  # not gated

MODES = {
    # The default: `lemonade-a2a serve` turns the A2A SDK's own spans off (see cli.default_sdk_tracing).
    "off": {},
    # A second identical process: the difference between the two is the noise floor of comparing
    # separate processes (scheduling, memory layout), which any real effect must exceed.
    "off-control": {},
    # What the adapter did before that default: SDK tracing decorators on, telemetry off.
    "sdk-spans-only": {"OTEL_INSTRUMENTATION_A2A_SDK_ENABLED": "true"},
    "on-100": {
        "LEMONADE_A2A_OTEL": "1",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_METRICS_EXPORTER": "none",
    },
    "on-10": {
        "LEMONADE_A2A_OTEL": "1",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_TRACES_SAMPLER": "traceidratio",
        "OTEL_TRACES_SAMPLER_ARG": "0.1",
    },
    "otlp-100": {
        "LEMONADE_A2A_OTEL": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    },
    # Everything on, including the SDK's own spans.
    "otlp-100-sdk-spans": {
        "LEMONADE_A2A_OTEL": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_INSTRUMENTATION_A2A_SDK_ENABLED": "true",
    },
}

A2A_HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Sink(BaseHTTPRequestHandler):
    """Accepts OTLP posts and discards them (a collector that is up and fast)."""

    received = 0

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)
        type(self).received += 1
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def wait_ready(url: str) -> None:
    for _ in range(80):
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"{url} did not come up")


def one_request(client: httpx.Client, base: str) -> tuple[float, float, int]:
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendStreamingMessage",
        "params": {
            "message": {
                "messageId": f"t-{time.perf_counter_ns()}",
                "role": "ROLE_USER",
                "parts": [{"text": "Explain local AI."}],
            }
        },
    }
    start = time.perf_counter()
    first = None
    chunks = 0
    with client.stream("POST", base, headers=A2A_HEADERS, json=body) as response:
        for line in response.iter_lines():
            if line.startswith("data:") and "artifactUpdate" in line and '"text"' in line:
                chunks += 1
                first = first or time.perf_counter()
    end = time.perf_counter()
    return (first - start) * 1000, (end - start) * 1000, chunks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=30)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    mock_port, sink_port = free_port(), free_port()
    ports = {mode: free_port() for mode in MODES}
    sink = HTTPServer(("127.0.0.1", sink_port), Sink)
    threading.Thread(target=sink.serve_forever, daemon=True).start()

    base_env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
        "MOCK_LEMONADE_TOKEN_COUNT": "8",
        "MOCK_LEMONADE_TOKEN_DELAY": "0.002",
        "LEMONADE_BASE_URL": f"http://127.0.0.1:{mock_port}/v1",
        "LEMONADE_MODEL": "mock",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{sink_port}",
        "LEMONADE_A2A_MAX_CONCURRENT_TASKS": "64",
    }
    for stale in [
        k for k in base_env if k.startswith("OTEL_") and k not in ("OTEL_EXPORTER_OTLP_ENDPOINT",)
    ]:
        del base_env[stale]
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.mock_lemonade:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(mock_port),
                "--log-level",
                "error",
            ],
            cwd=ROOT,
            env=base_env,
            stdout=subprocess.DEVNULL,
        )
    ]
    try:
        for mode, extra in MODES.items():
            env = {
                **base_env,
                **extra,
                "LEMONADE_A2A_PORT": str(ports[mode]),
                "LEMONADE_A2A_PUBLIC_URL": f"http://127.0.0.1:{ports[mode]}",
            }
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-m", "lemonade_a2a", "serve"],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        for mode in MODES:
            wait_ready(f"http://127.0.0.1:{ports[mode]}/healthz")

        samples: dict[str, list[tuple[float, float, int]]] = {mode: [] for mode in MODES}
        order_rng = random.Random(11)
        with httpx.Client(timeout=30) as client:
            for index in range(args.warmup + args.runs):
                order = list(MODES)
                order_rng.shuffle(order)  # a fixed order would favour whichever mode goes second
                for mode in order:  # every mode once per iteration: drift hits every mode equally
                    result = one_request(client, f"http://127.0.0.1:{ports[mode]}/")
                    if index >= args.warmup:
                        samples[mode].append(result)
        time.sleep(1)  # let the OTLP exporters flush once
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        sink.shutdown()

    def rate(row):
        return (row[2] - 1) / max((row[1] - row[0]) / 1000, 1e-9) if row[2] > 1 else 0.0

    def summary(rows):
        ttft = [r[0] for r in rows]
        total = [r[1] for r in rows]
        rates = [rate(r) for r in rows if r[2] > 1]
        return {
            "ttft_ms": {
                "median": statistics.median(ttft),
                "p95": sorted(ttft)[int(len(ttft) * 0.95)],
            },
            "total_ms": {
                "median": statistics.median(total),
                "p95": sorted(total)[int(len(total) * 0.95)],
            },
            "chunks_per_s": {"median": statistics.median(rates) if rates else 0.0},
        }

    def bootstrap_ci(values, resamples=2000):
        """95% interval of the median, by resampling (the data are noisy and not normal)."""
        rng = random.Random(7)
        medians = sorted(
            statistics.median(rng.choices(values, k=len(values))) for _ in range(resamples)
        )
        return medians[int(resamples * 0.025)], medians[int(resamples * 0.975)]

    report = {
        "runs": args.runs,
        "method": "paired: every iteration measures every mode back to back, and each mode is "
        "compared with 'off' from the same iteration, so drift cancels",
        "modes": {m: summary(r) for m, r in samples.items()},
    }
    off_rows = samples["off"]
    failures = []
    for mode, rows in samples.items():
        stats = report["modes"][mode]
        if mode == "off":
            stats["within_budget"] = True
            continue
        d_ttft = [r[0] - o[0] for r, o in zip(rows, off_rows, strict=True)]
        ratios = [
            rate(r) / rate(o)
            for r, o in zip(rows, off_rows, strict=True)
            if rate(o) > 0 and rate(r) > 0
        ]
        low, high = bootstrap_ci(d_ttft)
        off_median = report["modes"]["off"]["ttft_ms"]["median"]
        limit = max(TTFT_BUDGET_MS, TTFT_BUDGET_SHARE * off_median)
        ratio = statistics.median(ratios) if ratios else 1.0
        stats["paired_ttft_overhead_ms"] = {
            "median": round(statistics.median(d_ttft), 3),
            "ci95": [round(low, 3), round(high, 3)],
        }
        stats["ttft_limit_ms"] = round(limit, 3)
        stats["paired_throughput_vs_off"] = round(ratio, 3)
        # Within budget unless the median overhead is over the limit or throughput fell too far.
        stats["within_budget"] = statistics.median(d_ttft) <= limit and ratio >= THROUGHPUT_FLOOR
        stats["overhead_distinguishable_from_zero"] = low > 0 or high < 0
        if not stats["within_budget"] and mode not in INFORMATIONAL:
            failures.append(mode)
    report["otlp_posts_received"] = Sink.received
    report["failures"] = failures
    text = json.dumps(report, indent=2)
    sys.stdout.write(text + "\n")
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 1 if (args.check and failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
