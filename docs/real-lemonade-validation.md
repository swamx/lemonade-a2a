# Real Lemonade Validation

This is the P0 integration gate before proposing a native A2A implementation inside Lemonade.

## Goal

Prove the same A2A adapter that passes deterministic CI also works against a real Lemonade Server and real locally hosted model.

```text
A2A client
    │ A2A v1
    ▼
Lemonade A2A :9000
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
export LEMONADE_A2A_PUBLIC_URL=http://127.0.0.1:9000
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

## Follow-up gates

After the basic real-runtime test succeeds:

1. capture streaming TTFT direct vs A2A;
2. cancel an in-flight A2A task and verify the real Lemonade request stops;
3. validate backend/model-unavailable and timeout behavior;
4. run the official A2A compatibility tooling against the live adapter;
5. repeat on materially different Lemonade backend configurations.

## CI policy

The real-model validation should remain opt-in or self-hosted because hosted CI should not download large models or assume accelerator availability. Deterministic Mock Lemonade remains the mandatory pull-request gate.
