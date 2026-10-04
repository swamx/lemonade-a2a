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

### Real Lemonade runs

**Pending.** No Lemonade server was available on the machine used so far. Add rows as runs are collected (one per backend/model):

| Date | Lemonade / backend / model | Hardware | TTFT delta | Total delta | Cancellation | Report |
|---|---|---|---|---|---|---|
| _pending_ | | | | | | |

## Findings so far

- **Per-request HTTP client cost.** `LemonadeClient` created a new `httpx.AsyncClient` per request. Against the mock this added ~350 ms to TTFT (and to every non-streaming call) on Windows because each client builds a fresh SSL context. The adapter now reuses one pooled client; warm adapter TTFT overhead against the mock fell from ~360 ms to ~10 ms.
