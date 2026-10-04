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

No real-runtime results are recorded yet. The harness has only been exercised against the deterministic mock (`tests/mock_lemonade.py`), whose numbers are not evidence of real-model behaviour. Add reports here as they are collected:

| Date | Lemonade / backend / model | Hardware | TTFT Δ | Total Δ | Cancellation | Report |
|---|---|---|---|---|---|---|
| _pending_ | | | | | | |

## Findings so far

- **Per-request HTTP client cost.** `LemonadeClient` created a new `httpx.AsyncClient` per request. Against the mock this added ~350 ms to TTFT (and to every non-streaming call) on Windows because each client builds a fresh SSL context. The adapter now reuses one pooled client; warm adapter TTFT overhead against the mock fell from ~360 ms to ~13 ms.
