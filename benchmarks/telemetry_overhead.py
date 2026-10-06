"""What does OpenTelemetry cost? Adapter overhead with telemetry off, on, sampled and exporting.

    python benchmarks/telemetry_overhead.py [--runs 300] [--output result.json] [--check]

Starts a mock Lemonade and one adapter per mode as separate processes, then measures streaming
requests round-robin across the modes (so drift in the machine affects every mode equally):

    off        telemetry disabled (the default)
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

TTFT_BUDGET_MS = 1.0
TTFT_BUDGET_SHARE = 0.05
THROUGHPUT_FLOOR = 0.95

MODES = {
    "off": {},
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
        with httpx.Client(timeout=30) as client:
            for index in range(args.warmup + args.runs):
                for mode in MODES:  # round-robin: drift hits every mode equally
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

    def summary(rows):
        ttft = [r[0] for r in rows]
        total = [r[1] for r in rows]
        rate = [(r[2] - 1) / max((r[1] - r[0]) / 1000, 1e-9) for r in rows if r[2] > 1]
        return {
            "ttft_ms": {
                "median": statistics.median(ttft),
                "p95": sorted(ttft)[int(len(ttft) * 0.95)],
            },
            "total_ms": {
                "median": statistics.median(total),
                "p95": sorted(total)[int(len(total) * 0.95)],
            },
            "chunks_per_s": {"median": statistics.median(rate) if rate else 0.0},
        }

    report = {
        "runs": args.runs,
        "mock_total_ms_expected": "~15",
        "modes": {m: summary(r) for m, r in samples.items()},
    }
    off = report["modes"]["off"]
    failures = []
    for mode, stats in report["modes"].items():
        delta_ttft = stats["ttft_ms"]["median"] - off["ttft_ms"]["median"]
        limit = max(TTFT_BUDGET_MS, TTFT_BUDGET_SHARE * off["ttft_ms"]["median"])
        ratio = (
            stats["chunks_per_s"]["median"] / off["chunks_per_s"]["median"]
            if off["chunks_per_s"]["median"]
            else 1.0
        )
        stats["ttft_overhead_ms"] = round(delta_ttft, 3)
        stats["ttft_limit_ms"] = round(limit, 3)
        stats["throughput_vs_off"] = round(ratio, 3)
        stats["within_budget"] = mode == "off" or (
            delta_ttft <= limit and ratio >= THROUGHPUT_FLOOR
        )
        if not stats["within_budget"]:
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
