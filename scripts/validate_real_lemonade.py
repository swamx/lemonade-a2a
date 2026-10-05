"""One command to validate the adapter against a real Lemonade server.

    python scripts/validate_real_lemonade.py [--model ID] [--lemonade-url URL] [--runs 5]

Steps (each recorded in ``summary.json``; the exit code is non-zero if any fails):

1. diagnostics snapshot (versions, platform, Lemonade health/system info)
2. opt-in pytest integration suite (``tests/integration``)
3. starts one adapter and runs, against it,
   - ``scripts/real_lemonade_e2e.py``      (discovery, SendMessage, artifact)
   - ``benchmarks/cancellation_proof.py``  (CancelTask stops the backend)
   - ``benchmarks/benchmark_evidence.py``  (TTFT/latency/throughput vs direct)

Everything lands in ``reports/real-lemonade-<timestamp>/`` with per-step logs, so a
run can be attached to an issue as-is.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lemonade_a2a import diagnostics
from lemonade_a2a.config import Settings


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def discover_model(lemonade_url: str) -> str:
    data = httpx.get(f"{lemonade_url}/models", timeout=10).json().get("data") or []
    if not data:
        raise SystemExit("Lemonade reports no models; pull one first (`lemonade pull <model>`).")
    return data[0]["id"]


def run_step(name: str, argv: list[str], out_dir: Path, env: dict) -> dict:
    log = out_dir / f"{name}.log"
    started = time.perf_counter()
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            argv, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, check=False
        )
    step = {
        "name": name,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "seconds": round(time.perf_counter() - started, 1),
        "log": log.name,
    }
    print(f"{'PASS' if step['ok'] else 'FAIL'}  {name:<22} {step['seconds']:>6}s  ({log.name})")
    return step


def start_adapter(env: dict, port: int, out_dir: Path) -> subprocess.Popen:
    log = (out_dir / "adapter.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "lemonade_a2a.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                return process
        except httpx.HTTPError:
            time.sleep(0.5)
    process.kill()
    raise SystemExit(f"adapter did not start; see {out_dir / 'adapter.log'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lemonade-url", default=os.getenv("LEMONADE_BASE_URL", "http://127.0.0.1:13305/v1")
    )
    parser.add_argument("--model", default=os.getenv("LEMONADE_MODEL", ""))
    parser.add_argument("--runs", type=int, default=5, help="benchmark runs per target")
    parser.add_argument("--skip-benchmark", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    lemonade_url = args.lemonade_url.rstrip("/")
    try:
        model = args.model or discover_model(lemonade_url)
    except httpx.HTTPError as exc:
        raise SystemExit(f"Lemonade not reachable at {lemonade_url}: {exc}") from exc
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_dir or ROOT / "reports" / f"real-lemonade-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Lemonade {lemonade_url}  model {model}  ->  {out_dir}\n")

    settings = Settings(lemonade_base_url=lemonade_url, model=model)
    diagnostics.write(diagnostics.collect(settings), out_dir / "diagnostics.json")

    env = {**os.environ, "LEMONADE_BASE_URL": lemonade_url, "LEMONADE_MODEL": model}
    py = sys.executable
    steps = [
        run_step(
            "pytest-integration",
            [py, "-m", "pytest", "tests/integration", "-v", "-p", "no:cacheprovider"],
            out_dir,
            {**env, "LEMONADE_INTEGRATION": "1", "LEMONADE_DIAGNOSTICS_DIR": str(out_dir)},
        )
    ]

    port = free_port()
    a2a_url = f"http://127.0.0.1:{port}"
    adapter = start_adapter({**env, "LEMONADE_A2A_PUBLIC_URL": a2a_url}, port, out_dir)
    try:
        steps.append(
            run_step(
                "real-lemonade-e2e",
                [
                    py,
                    "scripts/real_lemonade_e2e.py",
                    "--a2a",
                    a2a_url,
                    "--lemonade",
                    lemonade_url,
                    "--model",
                    model,
                ],
                out_dir,
                env,
            )
        )
        steps.append(
            run_step(
                "cancellation-proof",
                [
                    py,
                    "benchmarks/cancellation_proof.py",
                    "--a2a-url",
                    a2a_url,
                    "--direct-url",
                    f"{lemonade_url}/chat/completions",
                    "--model",
                    model,
                ],
                out_dir,
                env,
            )
        )
        if not args.skip_benchmark:
            steps.append(
                run_step(
                    "benchmark",
                    [
                        py,
                        "benchmarks/benchmark_evidence.py",
                        "--a2a-url",
                        a2a_url,
                        "--direct-url",
                        f"{lemonade_url}/chat/completions",
                        "--model",
                        model,
                        "--warmup",
                        "1",
                        "--runs",
                        str(args.runs),
                        "--output",
                        str(out_dir / "benchmark.json"),
                    ],
                    out_dir,
                    env,
                )
            )
    finally:
        adapter.terminate()
        try:
            adapter.wait(timeout=10)
        except subprocess.TimeoutExpired:
            adapter.kill()

    summary = {
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "lemonade_url": lemonade_url,
        "model": model,
        "ok": all(step["ok"] for step in steps),
        "steps": steps,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n{'ALL PASSED' if summary['ok'] else 'FAILURES - see logs and diagnostics.json'}  ({out_dir})"
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
