# Evidence & Performance Benchmarks

`benchmarks/benchmark_evidence.py` compares **direct Lemonade** with **A2A → Lemonade** on the same model:

| Metric | How it is measured |
|---|---|
| TTFT | request start to first text chunk (direct SSE delta vs A2A `artifactUpdate`) |
| Total latency | request start to end of stream |
| Chunks/sec | streamed text chunks per second after the first (proxy for tokens/sec) |
| Cancellation | start a stream, `CancelTask` after the first chunk, expect `TASK_STATE_CANCELED` |
| Adapter RSS / idle CPU | optional, via `--a2a-pid` and `psutil` |

## Running it

```bash
# 1. Lemonade running with a model loaded, then the adapter on the same model
export LEMONADE_MODEL=<model-id>
lemonade-a2a &

# 2. Benchmark (writes benchmark-report.json and prints a Markdown table)
pip install psutil   # optional: adapter memory/CPU
python benchmarks/benchmark_evidence.py --runs 20 --a2a-pid <adapter-pid>
```

Record the Lemonade version, backend (CPU/GPU/NPU), model and hardware next to every report. Repeat per backend for the cross-backend comparison.

## Results

### Protocol overhead (mock backend) - 2026-10-04

Deterministic mock Lemonade (`tests/mock_lemonade.py`, 50 ms/token, 3 chunks), 20 runs after 3 warmups, median / p95, Windows 11 laptop, Python 3.11, adapter and mock on loopback. Raw data: [`benchmark-results/2026-10-04-mock-protocol-overhead.json`](benchmark-results/2026-10-04-mock-protocol-overhead.json).

| Metric | Direct | A2A -> backend | Delta (median) |
|---|---|---|---|
| TTFT (ms) | 4.2 / 5.8 | 14.0 / 18.9 | +9.8 |
| Total latency (ms) | 170.5 / 200.6 | 181.6 / 211.5 | +11.1 |
| Chunks/sec | 12.0 / 13.6 | 12.0 / 13.9 | -0.1 |
| Cancellation | - | pass | - |
| Adapter RSS (MB) | - | 68.3 | - |
| Adapter idle CPU (%) | - | 0.5 | - |

This isolates what A2A adds on top of an instant backend: roughly 10 ms of TTFT and total latency, with unchanged streaming throughput. It says nothing about model quality or real inference speed, and the mock does not exercise hardware backends.

### Real Lemonade runs - 2026-10-04

Lemonade Server 2026.40.0 (winget `AMD.LemonadeServer`), llama.cpp b10825, model `Bonsai-1.7B-gguf` (Q1_0, 0.23 GB), Windows 11, Intel i7-13700H, 32 GB RAM, NVIDIA RTX 4060 Laptop GPU. Adapter and Lemonade on loopback. 10 runs after 2 warmups, median / p95. The Lemonade `llamacpp.backend` setting was `auto` (resolved to the `gpu` device) and then `cpu`; the status table confirmed the device each time.

| Metric | GPU (auto): direct | GPU: A2A | CPU: direct | CPU: A2A |
|---|---|---|---|---|
| TTFT (ms) | 60.2 / 72.3 | 65.2 / 102.4 | 48.5 / 63.8 | 59.3 / 71.1 |
| Total latency (ms) | 436.3 / 517.0 | 407.5 / 483.9 | 939.0 / 1178.5 | 934.8 / 1152.4 |
| Chunks/sec | 146.8 / 161.8 | 142.0 / 152.7 | 54.3 / 61.8 | 57.2 / 61.4 |
| Cancellation | - | pass | - | pass |

Adapter footprint: ~69 MB RSS, 0-1% idle CPU.

Reading the numbers:

- **TTFT overhead is +5 ms (GPU) and +11 ms (CPU) at the median**, in line with the ~10 ms measured against the mock. Total-latency and throughput deltas are within run-to-run noise (generation length varies between runs, and some A2A medians are lower than direct), so they should not be read as A2A being faster.
- Cancellation returned `TASK_STATE_CANCELED` on both backends.
- Samples are small (10 runs, one short model, one machine). Treat these as an initial evidence point, not a performance guarantee. Not yet covered: larger models, NPU/ROCm backends, concurrent load, longer prompts and the official TCK against a real model.
- `scripts/real_lemonade_e2e.py` also passes against this setup.

Add rows below as further runs are collected:

| Date | Lemonade / backend / model | Hardware | TTFT delta | Total delta | Cancellation | Report |
|---|---|---|---|---|---|---|
| 2026-10-04 | 2026.40.0 / llamacpp gpu (auto) / Bonsai-1.7B | i7-13700H + RTX 4060 | +5.0 ms | -28.7 ms (noise) | pass | [json](benchmark-results/2026-10-04-bonsai-1.7b-llamacpp-gpu-auto.json) |
| 2026-10-04 | 2026.40.0 / llamacpp cpu / Bonsai-1.7B | i7-13700H | +10.8 ms | -4.3 ms (noise) | pass | [json](benchmark-results/2026-10-04-bonsai-1.7b-llamacpp-cpu.json) |

## Findings so far

- **Per-request HTTP client cost.** `LemonadeClient` created a new `httpx.AsyncClient` per request. Against the mock this added ~350 ms to TTFT (and to every non-streaming call) on Windows because each client builds a fresh SSL context. The adapter now reuses one pooled client; warm adapter TTFT overhead against the mock fell from ~360 ms to ~10 ms.

- **Port clash on a default install.** Lemonade Server's WebSocket listens on port 9000 (`lemonade status`), the adapter's default port. Run the adapter on another port (for example `LEMONADE_A2A_PORT=9100` with a matching `LEMONADE_A2A_PUBLIC_URL`) and pass `--a2a`/`--a2a-url` to the scripts.
