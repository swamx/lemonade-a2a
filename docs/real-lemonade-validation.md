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

**Re-validated for 0.2.0 on 2026-11-25** (same Windows machine, Lemonade Server 2026.40.0):

| Check | Result |
|---|---|
| `lemonade-a2a doctor --deep` (`Bonsai-1.7B-gguf`) | PASS: versions tested, model found (context 32,768), streaming generation |
| `scripts/validate_real_lemonade.py` | ALL PASSED: integration suite 6/6, end to end, cancellation proof (72 ms idle, 22.5 s queued behind a generation, 77 ms right after `CancelTask`), benchmark |
| A/B overhead against 0.1 (`99341a0`), 2 interleaved rounds × 30 runs each | No regression. Median TTFT overhead: 0.1 +30.1 / +14.4 ms, 0.2 +16.9 / +32.6 ms. Each version failed the 25 ms TTFT budget in one round and passed in the other, so on this machine both sit near the limit and the round-to-round spread (about 15 ms) is larger than any difference between them |
| Multi-agent pathway on a real model (planner, two specialists, a marketplace; 8 checks: marketplace listing, direct ask, `@mention`, two agents in parallel, planned delegation, a two-turn conversation, a file part) | `Gemma-3-4b-it-GGUF`: **8/8**. `Bonsai-1.7B-gguf`: 7/8; the 1.7B planner sent a question about a file to the weather agent (whose card takes no files) and repeated its invented answer |

What the multi-agent run found and fixed: a delegated call was cut off after the marketplace's 20 s *listing* timeout instead of the 120 s delegation timeout. A blocking send lasts as long as the remote task, and with one model, parallel calls queue in Lemonade. With the fix the same run had 10 delegations and no errors. Observed but not changed: small models sometimes copy the `<agent_reply>` fence tags into the final answer.

| Gate | Status |
|---|---|
| Real Lemonade + A2A `SendMessage` end to end | Done (`scripts/real_lemonade_e2e.py`) |
| Streaming TTFT, direct vs A2A | Done (`benchmarks/benchmark_evidence.py`) |
| Cancel an in-flight task | Done: task reaches `TASK_STATE_CANCELED` and the real backend stops generating (`benchmarks/cancellation_proof.py`: 30.9 s queued behind a running generation vs 177 ms right after `CancelTask`) |
| Backend unavailable / timeout behavior | Done: task ends `FAILED` (unit-tested; unreachable backend also checked by hand). Model-not-found is covered only through the generic non-2xx path |
| Official A2A TCK against the protocol surface | Done (see [conformance.md](conformance.md)); not run through a real model, by design |
| Materially different backends | llama.cpp CUDA, Vulkan and CPU, two models (Bonsai-1.7B, Gemma-3-4B); NPU, ROCm and non-llama.cpp engines not covered (no hardware) |
| Concurrent load | Done up to N = 8 on GPU backends: Lemonade serializes, the adapter adds no measurable cost |

## CI policy

The real-model validation should remain opt-in or self-hosted because hosted CI should not download large models or assume accelerator availability. Deterministic Mock Lemonade remains the mandatory pull-request gate.

> **Port note:** a default Lemonade install already uses port 9000 for its WebSocket, so the adapter defaults to 9100. If you change `LEMONADE_A2A_PORT`, set a matching `LEMONADE_A2A_PUBLIC_URL` and pass `--a2a <url>` to the scripts.

## Cross-checking an installation with `doctor`

Before (or instead of) the manual steps above, `lemonade-a2a doctor --deep` checks the whole chain in seconds: component versions against the tested manifest, the configuration, whether Lemonade answers (version from `/api/v1/health`), whether the configured model exists and how large its context window is, and a tiny streaming generation. A model with a small context window (Lemonade sizes it from available memory; Gemma-4-12B gets 1,459 tokens on an 8 GB GPU) is flagged because prompts longer than it fail with a clear message rather than completing empty.
