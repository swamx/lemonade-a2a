# Real Lemonade Validation

This is the P0 integration gate before proposing a native A2A implementation inside Lemonade.

## Goal

Prove the same A2A adapter that passes deterministic CI also works against a real Lemonade Server and real locally hosted model.

```text
A2A client
    │ A2A v1
    ▼
Lemonade A2A :9100
    │ OpenAI-compatible API
    ▼
Lemonade Server :13305
    │
    ▼
Lemonade Router / backend
    │
    ▼
Local AI System (CPU / GPU / NPU)
```

## Run

Start Lemonade Server using its normal documented workflow. Confirm its OpenAI-compatible API is available at `http://127.0.0.1:13305/v1`.

Then start the adapter:

```bash
export LEMONADE_BASE_URL=http://127.0.0.1:13305/v1
export LEMONADE_A2A_PUBLIC_URL=http://127.0.0.1:9100
lemonade-a2a
```

Run the validator:

```bash
python scripts/real_lemonade_e2e.py
```

The validator queries `/v1/models` and selects the first available model unless `--model` is supplied.

```bash
python scripts/real_lemonade_e2e.py --model <model-id>
```

## Required evidence

A successful validation must show:

- Lemonade `/v1/models` is reachable;
- an actual model is selected;
- A2A Agent Card discovery succeeds;
- A2A `SendMessage` succeeds;
- the request reaches real Lemonade inference;
- model-generated text returns as an A2A result/artifact.

## Status

Validated on 2026-10-04 against Lemonade Server 2026.40.0 with `Bonsai-1.7B-gguf` on llama.cpp GPU and CPU. Measurements are in [benchmarks.md](benchmarks.md).

| Gate | Status |
|---|---|
| Real Lemonade + A2A `SendMessage` end to end | Done (`scripts/real_lemonade_e2e.py`) |
| Streaming TTFT, direct vs A2A | Done (`benchmarks/benchmark_evidence.py`) |
| Cancel an in-flight task | Done: task reaches `TASK_STATE_CANCELED`. Whether the real Lemonade backend request also stops is not yet verified |
| Backend unavailable / timeout behavior | Done: task ends `FAILED` (unit-tested; unreachable backend also checked by hand). Model-not-found is covered only through the generic non-2xx path |
| Official A2A TCK/Inspector against the live adapter | Not run |
| Materially different backends | CPU and GPU (llama.cpp) done; NPU/ROCm not covered |

## CI policy

The real-model validation should remain opt-in or self-hosted because hosted CI should not download large models or assume accelerator availability. Deterministic Mock Lemonade remains the mandatory pull-request gate.

> **Port note:** a default Lemonade install already uses port 9000 for its WebSocket, so the adapter defaults to 9100. If you change `LEMONADE_A2A_PORT`, set a matching `LEMONADE_A2A_PUBLIC_URL` and pass `--a2a <url>` to the scripts.
