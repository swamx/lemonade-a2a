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
lemonade-a2a &          # listens on :9100

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

### Backend and model matrix - 2026-10-04

Same machine and software as above plus the llama.cpp **CUDA** backend and a larger model, `Gemma-3-4b-it-GGUF` (Q4_K_M, 3.1 GB). Each row: 10 runs after 2 warmups, direct / A2A, median (TTFT also shows p95). The backend was selected with `lemonade config set llamacpp.backend=<b>` and verified from the running `llama-server.exe` path (`cuda`, `vulkan`, `cpu`); Lemonade's own device column only says `gpu` for both CUDA and Vulkan.

| Model | Backend | TTFT direct (med / p95 ms) | TTFT A2A (med / p95 ms) | TTFT delta | Total ms (direct / A2A) | Chunks/s (direct / A2A) | Cancel |
|---|---|---|---|---|---|---|---|
| Bonsai-1.7B-gguf | cuda | 60 / 98 | 98 / 126 | +39 | 266 / 311 | 240 / 226 | pass |
| Bonsai-1.7B-gguf | vulkan | 70 / 134 | 88 / 142 | +18 | 423 / 452 | 147 / 137 | pass |
| Bonsai-1.7B-gguf | cpu | 51 / 61 | 60 / 76 | +9 | 893 / 894 | 60 / 61 | pass |
| Gemma-3-4b-it-GGUF | cuda | 101 / 132 | 136 / 173 | +35 | 1000 / 1036 | 63 / 63 | pass |
| Gemma-3-4b-it-GGUF | vulkan | 107 / 223 | 111 / 182 | +4 | 1004 / 1014 | 66 / 65 | pass |
| Gemma-3-4b-it-GGUF | cpu | 135 / 156 | 144 / 161 | +9 | 3443 / 3810 | 18 / 18 | pass |

Repeat on Bonsai-1.7B with 30 runs per backend to check the larger CUDA deltas: CUDA +9 ms and +14 ms (two separate runs), Vulkan +13 ms. Individual 10-run medians moved by tens of milliseconds between identical configurations (the *direct* baseline alone varied 41-62 ms on CUDA), so the honest summary is **median TTFT overhead of roughly 5-15 ms, with single 10-run samples up to about +40 ms**. Total latency and chunks/sec differences stay within run-to-run noise. Raw data: `benchmark-results/2026-10-04-*-matrix.json` and `*-30run.json` (the CUDA 30-run file is the second of the two runs).

The adapter's footprint on the Bonsai runs was 69-75 MB RSS and ~0% idle CPU. The footprint reading for the Gemma runs (5 MB) came from sampling the wrong process and is discarded.

### Concurrent load - 2026-10-04

Bursts of N simultaneous streaming requests, repeated (3 rounds; 2 rounds for Gemma on CPU, which was also limited to N <= 4 for time), against Lemonade directly and through the adapter. The adapter's concurrency cap was raised to 32 so nothing was rejected; every request succeeded. Cells are direct / A2A.

| Model | Backend | N | OK (direct / A2A) | Req/s | TTFT median ms | Total median ms |
|---|---|---|---|---|---|---|
| Bonsai-1.7B-gguf | cuda | 1 | 3/3 / 3/3 | 2.79 / 2.86 | 94 / 127 | 296 / 351 |
| Bonsai-1.7B-gguf | cuda | 2 | 6/6 / 6/6 | 3.80 / 3.68 | 205 / 216 | 409 / 414 |
| Bonsai-1.7B-gguf | cuda | 4 | 12/12 / 12/12 | 3.93 / 4.04 | 444 / 436 | 647 / 666 |
| Bonsai-1.7B-gguf | cuda | 8 | 24/24 / 24/24 | 4.18 / 3.95 | 921 / 969 | 1127 / 1216 |
| Bonsai-1.7B-gguf | vulkan | 1 | 3/3 / 3/3 | 1.93 / 2.34 | 77 / 73 | 421 / 427 |
| Bonsai-1.7B-gguf | vulkan | 2 | 6/6 / 6/6 | 2.39 / 2.33 | 236 / 288 | 595 / 671 |
| Bonsai-1.7B-gguf | vulkan | 4 | 12/12 / 12/12 | 2.55 / 2.34 | 649 / 717 | 960 / 1104 |
| Bonsai-1.7B-gguf | vulkan | 8 | 24/24 / 24/24 | 2.40 / 2.38 | 1547 / 1624 | 1956 / 1995 |
| Bonsai-1.7B-gguf | cpu | 1 | 3/3 / 3/3 | 1.03 / 1.09 | 48 / 58 | 979 / 904 |
| Bonsai-1.7B-gguf | cpu | 2 | 6/6 / 6/6 | 1.16 / 1.14 | 490 / 490 | 1294 / 1354 |
| Bonsai-1.7B-gguf | cpu | 4 | 12/12 / 12/12 | 1.17 / 1.15 | 1318 / 1283 | 2195 / 2202 |
| Bonsai-1.7B-gguf | cpu | 8 | 24/24 / 24/24 | 1.17 / 1.12 | 3097 / 3032 | 3990 / 4045 |
| Gemma-3-4b-it-GGUF | cuda | 1 | 3/3 / 3/3 | 0.93 / 0.96 | 110 / 137 | 993 / 1041 |
| Gemma-3-4b-it-GGUF | cuda | 2 | 6/6 / 6/6 | 1.03 / 0.96 | 580 / 629 | 1479 / 1588 |
| Gemma-3-4b-it-GGUF | cuda | 4 | 12/12 / 12/12 | 0.99 / 0.99 | 1611 / 1611 | 2575 / 2507 |
| Gemma-3-4b-it-GGUF | cuda | 8 | 24/24 / 24/24 | 0.98 / 1.00 | 3781 / 3597 | 4722 / 4511 |
| Gemma-3-4b-it-GGUF | vulkan | 1 | 3/3 / 3/3 | 0.96 / 0.97 | 164 / 108 | 1073 / 1051 |
| Gemma-3-4b-it-GGUF | vulkan | 2 | 6/6 / 6/6 | 0.94 / 0.96 | 649 / 632 | 1605 / 1569 |
| Gemma-3-4b-it-GGUF | vulkan | 4 | 12/12 / 12/12 | 0.99 / 0.99 | 1592 / 1597 | 2536 / 2465 |
| Gemma-3-4b-it-GGUF | vulkan | 8 | 24/24 / 24/24 | 0.97 / 0.99 | 3819 / 3524 | 4694 / 4403 |
| Gemma-3-4b-it-GGUF | cpu | 1 | 2/2 / 2/2 | 0.30 / 0.26 | 181 / 152 | 3344 / 3784 |
| Gemma-3-4b-it-GGUF | cpu | 2 | 4/4 / 4/4 | 0.30 / 0.29 | 1769 / 1728 | 4964 / 5102 |
| Gemma-3-4b-it-GGUF | cpu | 4 | 8/8 / 8/8 | 0.28 / 0.28 | 5278 / 5532 | 8731 / 8893 |

What this shows:

- **Lemonade/llama.cpp serves these requests one at a time**: throughput stays flat as N grows (about 1 req/s for Gemma on GPU, 4 req/s for Bonsai on CUDA) while TTFT and total latency grow roughly linearly with N. That queueing is in the backend, not the adapter.
- **The adapter adds no measurable throughput or queueing cost**: A2A and direct are within noise at every N, and no request failed at N = 8.
- With the adapter's default cap (`LEMONADE_A2A_MAX_CONCURRENT_TASKS=8`) a burst larger than the cap gets `TASK_STATE_REJECTED` for the excess instead of queueing.

### Cancellation reaches the real backend - 2026-10-04

`benchmarks/cancellation_proof.py` times a tiny direct request (Gemma-3-4B, llama.cpp CUDA): 167 ms on an idle server, **30.9 s** while an A2A-started essay is still generating (it queues behind it), and **177 ms** immediately after `CancelTask` on such an essay (`TASK_STATE_CANCELED`). The backend stops generating when the A2A task is cancelled; it is not just task metadata changing.

### What this machine cannot cover

This laptop has an Intel CPU and an NVIDIA GPU. **NPU, ROCm and non-llama.cpp engines (ONNX Runtime/Ryzen AI, FastFlowLM) were not measured** because the hardware is absent; ONNX Runtime is installable in Lemonade but the catalog models for it here are not chat models. Those rows remain open in the roadmap.

## Findings so far

- **Per-request HTTP client cost.** `LemonadeClient` created a new `httpx.AsyncClient` per request. Against the mock this added ~350 ms to TTFT (and to every non-streaming call) on Windows because each client builds a fresh SSL context. The adapter now reuses one pooled client; warm adapter TTFT overhead against the mock fell from ~360 ms to ~10 ms.

- **Port clash on a default install.** Lemonade Server's WebSocket listens on port 9000 (`lemonade status`), which was the adapter's original default. The adapter now defaults to 9100; if you change `LEMONADE_A2A_PORT`, set a matching `LEMONADE_A2A_PUBLIC_URL` and pass `--a2a`/`--a2a-url` to the scripts.
- **Client disconnect does not stop generation (by design).** Closing a streaming connection mid-generation leaves the task, and the Lemonade request behind it, running to completion; A2A tasks outlive their connections and can be resubscribed. Wasted work is bounded by the per-task deadline (`LEMONADE_A2A_MAX_TASK_SECONDS`, default 600 s) and stopped explicitly by `CancelTask`.
- **A route-ordering regression was caught by the official TCK**, not by unit tests (HTTP+JSON `GET /tasks/{id}` returned a bare 404 after the legacy-prefix reorder). Tests now assert the A2A error body on both prefixes.
