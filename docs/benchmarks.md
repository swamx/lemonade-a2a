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

### Matrix v2: larger model, long prompts, long outputs, more runs - 2026-10-04

Raw reports: [benchmark-results/matrix-v2/](benchmark-results/matrix-v2/). Medians, direct / A2A, with the A2A delta in parentheses (ms). Workloads: `short` (6-word prompt), `long-prompt` (~1,500-word prompt), `long-output` (asks for ~400 words). 30 runs for short cells on the 1.7B and 4B models, 15 / 8-10 for the longer workloads, 20 for the 12B model.

| Model | Backend | Workload | Runs | TTFT ms | Total ms | Chunks/s | Budget |
|---|---|---|---|---|---|---|---|
| Bonsai-1.7B-gguf | cpu | long-output | 10 | 70 / 84 (+14) | 11909 / 11633 (-276) | 42.2 / 43.4 | within |
| Bonsai-1.7B-gguf | cpu | long-prompt | 15 | 60 / 76 (+15) | 1292 / 1272 (-20) | 36.5 / 35.6 | within |
| Bonsai-1.7B-gguf | cpu | short | 30 | 53 / 64 (+10) | 1178 / 1126 (-53) | 45.3 / 47.3 | within |
| Bonsai-1.7B-gguf | cuda | long-output | 10 | 79 / 83 (+3) | 1868 / 2160 (+292) | 268.4 / 254.5 | OVER |
| Bonsai-1.7B-gguf | cuda | long-prompt | 15 | 62 / 79 (+16) | 249 / 297 (+49) | 219.9 / 194.4 | OVER |
| Bonsai-1.7B-gguf | cuda | short | 30 | 46 / 51 (+5) | 229 / 253 (+24) | 288.2 / 263.2 | within |
| Bonsai-1.7B-gguf | vulkan | long-output | 10 | 115 / 126 (+11) | 3844 / 3644 (-201) | 128.0 / 136.5 | within |
| Bonsai-1.7B-gguf | vulkan | long-prompt | 15 | 68 / 79 (+11) | 415 / 481 (+66) | 118.4 / 115.1 | OVER |
| Bonsai-1.7B-gguf | vulkan | short | 30 | 74 / 90 (+16) | 458 / 467 (+10) | 134.8 / 135.2 | within |
| Gemma-3-4b-it-GGUF | cpu | long-output | 8 | 163 / 181 (+18) | 36991 / 36432 (-559) | 17.2 / 17.3 | within |
| Gemma-3-4b-it-GGUF | cpu | long-prompt | 15 | 159 / 173 (+13) | 3120 / 3128 (+8) | 15.2 / 15.3 | within |
| Gemma-3-4b-it-GGUF | cpu | short | 30 | 136 / 142 (+6) | 3525 / 3615 (+90) | 17.8 / 17.8 | within |
| Gemma-3-4b-it-GGUF | cuda | long-output | 8 | 137 / 163 (+26) | 10330 / 9868 (-462) | 64.4 / 65.4 | within |
| Gemma-3-4b-it-GGUF | cuda | long-prompt | 15 | 178 / 203 (+25) | 943 / 991 (+48) | 59.0 / 57.4 | within |
| Gemma-3-4b-it-GGUF | cuda | short | 30 | 111 / 122 (+11) | 1013 / 1039 (+26) | 62.6 / 61.9 | within |
| Gemma-3-4b-it-GGUF | vulkan | long-output | 8 | 239 / 253 (+14) | 11831 / 11730 (-102) | 56.9 / 56.3 | within |
| Gemma-3-4b-it-GGUF | vulkan | long-prompt | 15 | 284 / 260 (-24) | 1123 / 1102 (-21) | 53.5 / 53.1 | within |
| Gemma-3-4b-it-GGUF | vulkan | short | 30 | 142 / 168 (+26) | 1172 / 1204 (+32) | 56.8 / 55.2 | within |
| Gemma-4-12B-it-GGUF | cuda | short | 20 | 20399 / 21932 (+1533) | 23233 / 24873 (+1640) | 17.1 / 16.9 | OVER |

**Overhead budget** (enforced by `benchmarks/check_budget.py`, which `bench.yml` runs). Each limit is the larger of an absolute floor and a share of the direct figure, so a slow model is not held to a sub-millisecond limit:

| Metric | Limit |
|---|---|
| TTFT overhead | max(25 ms, 25% of direct TTFT) |
| Total-latency overhead | max(50 ms, 5% of direct total) |
| Streaming throughput | at least 90% of direct chunks/s |
| Adapter resident memory | at most 100 MB |
| Adapter idle CPU | at most 2% |

Reports with fewer than 20 runs are scored but only *indicative*: laptop run-to-run noise is tens of milliseconds, larger than the budget floors.

**Reading the matrix honestly**

- **Every 30-run cell on the 1.7B and 4B models is within budget.** That covers CUDA, Vulkan and CPU, so the adapter's cost on short requests is small and does not depend on the backend.
- **Three short-sample cells (10-15 runs) are over budget and are indicative only**: Bonsai CUDA long-output (+292 ms total), Bonsai CUDA long-prompt (88% throughput), Bonsai Vulkan long-prompt (+66 ms total). The same cells in the 30-run short workload are within budget; a 10-run median on a 1.7B model whose total time is a few hundred ms is within the noise. They should be re-run with 20+ runs (the Linux `bench.yml` job does) before reading anything into them.
- **Gemma-4-12B on CUDA looked over budget in this 20-run cell (+1,640 ms total, +1,533 ms TTFT) and that was later refuted as noise** by a 30-run repeat (+496 ms total, +87 ms TTFT, within budget); see [Gemma-4-12B re-run](#gemma-4-12b-re-run-2026-10-06). The 20-run cell is kept here as recorded.
- **Gemma-4-12B was only partially measured in this matrix, and the first explanation was wrong.** The long-prompt and long-output cells failed with `stream produced no text chunks`. I first blamed reasoning models (the model does stream `reasoning_content` before its answer), but the cells failed within a second, which thinking cannot explain. The harness now reports why a stream is empty, and the real cause was an error event *inside* a 200 stream: `request (1647 tokens) exceeds the available context size (1536 tokens)`. Lemonade sizes a model's context from available memory, and this model gets about 1,459 tokens on an 8 GB GPU. The long-prompt workload (1,492 words) can never fit; see the re-run below, which also records the adapter defect this exposed.
- **The laptop numbers are from one machine; the second machine is GitHub's Linux runner.** `bench.yml` ran on the pull request that added it (an `ubuntu-latest` runner, mock backend, 30 runs): TTFT **+5.5 ms** (1.8 ms direct, 7.4 ms through A2A), total latency **+5.7 ms**, identical throughput (15.9 chunks/s), within the budget. That confirms the *protocol* cost on a different OS and CPU; it does not measure real inference on other hardware.

### Gemma-4-12B re-run - 2026-10-06

Closes the carry-over from the matrix above. Raw reports: [benchmark-results/2026-10-06-gemma-4-12b/](benchmark-results/2026-10-06-gemma-4-12b/). Same machine (RTX 4060 laptop, 8 GB), Lemonade 2026.40.0, llama.cpp CUDA unless noted; medians, direct / A2A.

| Cell | Runs | TTFT ms | Total ms | Chunks/s | Budget |
|---|---|---|---|---|---|
| short, CUDA (repeat of the 20-run cell that looked over budget) | **30** | 21138 / 21225 (**+87**) | 24027 / 24523 (**+496**) | 16.9 / 16.8 | **within** (limit 1,201 ms) |
| long output (~400 words), CUDA | 5 | 60269 / 51520 (-8748) | 92097 / 91482 (-615) | 15.5 / 15.4 | within (indicative, 5 runs) |
| medium prompt (~670 words), CUDA, first try | 6 | 18419 / 24240 (+5821) | 21029 / 26902 (+5873) | 15.9 / 15.8 | over, **and noise** (below) |
| medium prompt, CUDA, 20 runs, thinking forwarded | **20** | 18765 / 19086 (**+320**) | 21532 / 21918 (**+386**) | 15.4 / 15.4 | **within** (limit 1,077 ms) |
| medium prompt, same, **first output of any kind** | 20 | 589 / 596 (**+7**) | | | **within** |
| short, **CPU** (12B on 20 logical cores) | 4 | 52860 / 57245 (+4384) | 61629 / 65736 (+4107) | 6.1 / 6.2 | indicative only (4 runs, direct p95 72 s) |
| long prompt (1,492 words) | - | - | - | - | **cannot run**: the model loads with a 1,459-token context |

**What these show**

- **The +1.6 s on CUDA was noise.** With 30 runs the adapter cost on a 24 s request is +87 ms to the first answer chunk and +496 ms in total, inside the budget; the direct runs alone ranged from 14 s to 37 s to the first token.
- **Reasoning models make "time to first token" a poor overhead metric.** Gemma-4-12B thinks (about 290 to 300 reasoning chunks per run here) before it answers, and the length of that thinking is random. The 6-run medium-prompt cell reported +5.8 s that way, then 20 runs reported +320 ms. The harness therefore now also records the **time to the first output of any kind** (reasoning or answer; the adapter's `LEMONADE_A2A_REASONING=artifact` mode forwards the thinking so both sides can be compared) and the number of reasoning chunks per run: the adapter adds **7 ms** there, and the thinking length was the same on both sides (291 vs 296 chunks). `ttft_any_ms` and `think_chunks` are in the report format; `check_budget.py` still scores the answer-chunk TTFT.
- **Throughput is identical** everywhere (15 to 17 chunks/s on CUDA, 6 on CPU): the adapter does not slow generation.
- **The long-prompt cell is a hardware limit, not an adapter result.** Lemonade sizes a model's context from available memory; this model loads with `--ctx-size 1459`, so a 1,647-token prompt is refused by the server. That refusal arrives as an error *inside* a 200 stream, which exposed an adapter defect (it completed the task with an empty answer); it now fails with "The request is longer than the model's context window." and is in the contract tests. The new `medium-prompt` workload fits. `lemonade-a2a doctor` flags a context window below 4,096 tokens.
- **CPU, 4 runs only.** 12B on CPU takes about a minute per answer, so the cell is indicative: +4.4 s on a 53 to 72 s spread, with the same throughput. It shows no sign of adapter-specific slowdown; it does not establish a number.
- **Memory.** The adapter's RSS was 74 MB in the CPU run and 98 MB in the 20-run run that forwards reasoning as artifacts: with that mode every stored task holds its thinking text, so footprint grows with the task store (bounded by `LEMONADE_A2A_MAX_STORED_TASKS`). The default mode drops the thinking.

### OpenTelemetry overhead

Method, modes and the revised budget are in [observability.md](observability.md#6-performance); results in [benchmark-results/2026-10-07-telemetry-overhead.json](benchmark-results/2026-10-07-telemetry-overhead.json). Paired against `off` on the mock (about 40 ms per request): instrumentation **+1.1 ms**, with 10% sampling +0.5 ms, with full OTLP export **+1.9 ms**, throughput unchanged (0.99 to 1.01), a noise floor of **±0.4 ms** (an identical second `off` process: +0.07 ms). All within the revised 2.5 ms / 5% budget.

The measurement also showed that the **A2A SDK's own tracing decorators cost +15.6 ms per request (throughput halved) in a bursty workload even with telemetry off**, and 2 to 5 ms in a warm sequential one. `lemonade-a2a serve` now turns them off by default. A direct comparison of this branch with `main` on the same mock (60 runs, 8 tokens 2 ms apart) confirmed there was no regression from this release's added middleware: **+28.7 ms TTFT on `main` (SDK spans on), +27.8 ms on this branch with SDK spans on, +26.3 ms with them off** over the direct path. Those protocol-overhead figures are higher than the +10 ms recorded on 2026-10-04 for the same kind of mock run; the machine was noisier today, and both `main` and this branch moved together. Treat absolute mock overheads on this laptop as ±10 ms and read the paired differences.

### Disconnect: default vs cancel-on-disconnect (real Lemonade) - 2026-10-05

`benchmarks/disconnect_proof.py` streams a ~600-word generation (Gemma-3-4B, Lemonade `auto` backend), drops the connection after the first chunk, and times a tiny direct request. Result: [2026-10-05-cancel-on-disconnect-gemma-3-4b-cuda.json](benchmark-results/2026-10-05-cancel-on-disconnect-gemma-3-4b-cuda.json).

| | Tiny request after the client left | Task state afterwards |
|---|---|---|
| Idle server (reference) | 0.37 s | - |
| Default (task outlives its connection) | **15.1 s**: queued behind the abandoned generation | `COMPLETED` |
| `LEMONADE_A2A_CANCEL_ON_DISCONNECT=1` | **1.1 s** | `CANCELED` |

So the default really does burn backend time on output nobody is reading, which is why the opt-in exists. Cancel-on-disconnect frees the backend, though the first request afterwards is still about 0.8 s slower than on a never-used server (the aborted generation takes a moment to unwind; one measurement, not a distribution).

### What this machine cannot cover

This laptop has an Intel CPU and an NVIDIA GPU. **NPU, ROCm and non-llama.cpp engines (ONNX Runtime/Ryzen AI, FastFlowLM) were not measured** because the hardware is absent; ONNX Runtime is installable in Lemonade but the catalog models for it here are not chat models. Those rows remain open in the roadmap.

## Findings so far

- **Per-request HTTP client cost.** `LemonadeClient` created a new `httpx.AsyncClient` per request. Against the mock this added ~350 ms to TTFT (and to every non-streaming call) on Windows because each client builds a fresh SSL context. The adapter now reuses one pooled client; warm adapter TTFT overhead against the mock fell from ~360 ms to ~10 ms.

- **Port clash on a default install.** Lemonade Server's WebSocket listens on port 9000 (`lemonade status`), which was the adapter's original default. The adapter now defaults to 9100; if you change `LEMONADE_A2A_PORT`, set a matching `LEMONADE_A2A_PUBLIC_URL` and pass `--a2a`/`--a2a-url` to the scripts.
- **Client disconnect does not stop generation (by default).** Opt in to cancelling it with `LEMONADE_A2A_CANCEL_ON_DISCONNECT=1` (see [protocol-mapping.md](protocol-mapping.md)). Closing a streaming connection mid-generation leaves the task, and the Lemonade request behind it, running to completion; A2A tasks outlive their connections and can be resubscribed. Wasted work is bounded by the per-task deadline (`LEMONADE_A2A_MAX_TASK_SECONDS`, default 600 s) and stopped explicitly by `CancelTask`.
- **A route-ordering regression was caught by the official TCK**, not by unit tests (HTTP+JSON `GET /tasks/{id}` returned a bare 404 after the legacy-prefix reorder). Tests now assert the A2A error body on both prefixes.
