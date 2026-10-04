# Roadmap / TODO

This roadmap tracks the path from the Python reference adapter to a potential native Lemonade A2A protocol surface.

The architectural rule is simple: **A2A owns agent interoperability; Lemonade owns inference, model management, backend routing and hardware optimization.** The A2A layer must remain usable across local AI systems and must not depend on AMD-specific hardware or a separate reasoning-model stack.

_Last reviewed: 2026-10-04._ A checked box means the item is implemented **and** verified (test, CI or recorded measurement); caveats are written next to the item.

## Where we are

| Area | Status |
|---|---|
| Protocol surface (Agent Card, JSON-RPC, HTTP+JSON, streaming, task lifecycle, cancellation) | Implemented, unit/E2E tested |
| Deterministic mock E2E and CI (3.11-3.13, ruff, ruff format) | Green |
| Real Lemonade validation (2026.40.0, llama.cpp GPU + CPU, Bonsai-1.7B) | Done, small sample |
| Performance evidence (TTFT, latency, throughput, RSS/CPU) | First data points: +5 to +11 ms TTFT |
| Failure handling (backend down/timeout/non-2xx, bad input) | Implemented and tested |
| Official A2A TCK / ITK results | **Not run** (`docs/conformance-results.json` is a placeholder) |
| Resource bounds, auth/TLS, backpressure, disconnect propagation | **Not implemented** |
| NPU / ROCm / non-llama.cpp backends, larger models, concurrent load | **Not measured** |
| Native Lemonade integration | Not started (by design, see gates below) |

## Done

- [x] Core A2A/Lemonade integration boundary, official `AgentExecutor`, request handler, in-memory task store.
- [x] Lemonade OpenAI-compatible client with SSE parsing, one pooled HTTP client (removed ~350 ms of per-request overhead on Windows).
- [x] Agent Card factory and discovery; hardware-agnostic content enforced by tests; example card checked against the generated card.
- [x] JSON-RPC and HTTP+JSON served at the advertised base URL, legacy `/a2a/*` paths kept and tested (they were previously shadowed by the `/{tenant}` mount).
- [x] Streaming bridge, cancellation into the active inference coroutine, `TASK_NOT_CANCELABLE` to HTTP 409, `application/json` REST errors.
- [x] Input validation: non-text parts rejected, empty/oversized text is `InvalidParams`.
- [x] Backend unreachable / timeout / non-2xx ends the task in `TASK_STATE_FAILED` with a sanitized message (previously a raw internal error with a stack trace).
- [x] Validated `Settings` (port range, positive limits), configurable timeout and input limit, version read from package metadata.
- [x] Removed the stale duplicate Agent Card builder.
- [x] Default port moved from 9000 to 9100 (9000 is Lemonade's WebSocket port).
- [x] CI: ruff, ruff format check, pytest matrix, mock-Lemonade black-box job, protocol unit tests.
- [x] Configurable mock Lemonade (`MOCK_LEMONADE_RESPONSES`, `MOCK_LEMONADE_TOKEN_DELAY`) without suite-specific hardcoding.

## P0 — Conformance evidence — NEXT

The adapter has been adjusted to TCK expectations, but there is no recorded result. This is now the biggest evidence gap.

- [ ] Run the official A2A TCK against the live adapter (mock backend, pinned TCK revision).
- [ ] Run the A2A Inspector and/or ITK cross-SDK scenarios.
- [ ] Populate `docs/conformance-results.json` from reproducible runs (TCK commit, adapter commit, pass/fail/skip per transport).
- [ ] Fix or document every failing case with an upstream link where relevant.
- [ ] Add an opt-in CI job that runs the pinned TCK.

**Exit criterion:** a published pass/fail matrix tied to exact TCK and adapter commits.

## P0 — Real Lemonade validation — mostly done

- [x] Real-Lemonade validator (`scripts/real_lemonade_e2e.py`): model discovery, Agent Card → `SendMessage` → artifact.
- [x] Real SSE streaming and final artifact assembly (via `benchmarks/benchmark_evidence.py`).
- [x] Backend-error and timeout mapping (unit-tested; unreachable backend also checked by hand).
- [x] Cancellation reaches `TASK_STATE_CANCELED` on a real backend.
- [ ] Verify cancellation actually **stops the real backend request** (e.g. Lemonade/llama.cpp stops generating), not only the adapter task.
- [ ] Convert the validator into an opt-in `pytest` integration test.
- [ ] Capture service logs and environment metadata automatically on failure.
- [ ] One-command local workflow (start adapter, run validator, run benchmark).
- [ ] Validate with an official A2A client, not only the in-repo scripts.

## P0 — Performance evidence

- [x] Direct vs A2A harness: TTFT, total latency, chunks/sec, cancellation, adapter RSS/CPU, median and p95.
- [x] Protocol overhead measured independently of generation time (mock backend: ~+10 ms TTFT).
- [x] First real results: Bonsai-1.7B, llama.cpp GPU (+5 ms TTFT) and CPU (+11 ms TTFT); adapter ~69 MB RSS, ~0-1% idle CPU.
- [ ] Larger models and longer prompts/outputs.
- [ ] Concurrent tasks (throughput and latency under load).
- [ ] More runs and more than one machine (current sample is 10 runs on one laptop).
- [ ] Define an acceptable overhead budget before native implementation.

The objective is not to optimize the Python adapter indefinitely; it is to establish a baseline that tells us whether native Lemonade integration is justified and what it must improve.

## P1 — Lifecycle and resilience

- [x] Active inference cancellation propagation.
- [x] Request timeout policy (`LEMONADE_TIMEOUT_SECONDS`).
- [x] Lemonade unavailable / non-2xx mapped to `FAILED` (model-not-found goes through the generic non-2xx path; no dedicated message).
- [ ] Bounded streaming queues/backpressure.
- [ ] Client disconnect propagation to the backend request.
- [ ] Concurrent task isolation tests.
- [ ] Repeated cancellation/race-condition tests.
- [ ] Graceful server shutdown with active tasks.
- [ ] Close partial artifacts cleanly when a stream fails mid-way.

## P1 — Security and compatibility hardening

- [x] Automated smoke conformance in CI.
- [x] Message size limit (`LEMONADE_A2A_MAX_INPUT_CHARS`).
- [ ] Limits on number/size of parts, concurrent tasks, task lifetime and stored history (the in-memory task store is unbounded).
- [ ] Fuzz malformed messages, parts and metadata.
- [ ] Validate safe URL/file handling before enabling richer parts.
- [ ] Define local-only, LAN and externally exposed security profiles.
- [ ] Authentication/TLS guidance for non-loopback deployments.
- [ ] Publish a formal security policy.

## P1 — Prove local-AI-system portability

A2A must remain independent of the accelerator selected by Lemonade.

- [x] Two materially different execution paths on one machine: llama.cpp CPU and llama.cpp GPU (auto), same adapter and behavior.
- [x] No A2A protocol behavior or Agent Card content depends on AMD-specific metadata (tested).
- [ ] An engine other than llama.cpp (e.g. ONNX Runtime / Ryzen AI / FastFlowLM) and an NPU path where hardware allows.
- [ ] ROCm and Metal paths where hardware allows.
- [ ] Keep optional hardware metadata informational and extension-based.
- [ ] Use **local AI system** terminology in generic architecture documentation; reserve AMD-specific language for AMD challenge/optimization material.

## P2 — Lemonade-native architecture proposal

The desired end state is not a permanent second inference server. A2A should become another protocol surface feeding Lemonade's existing router/model/backend architecture.

```text
A2A clients                 Existing API clients
     │                             │
     └──────────────┬──────────────┘
                    ▼
              Lemonade Server
                    │
       ┌────────────┴────────────┐
       │ Protocol / HTTP layer   │
       │ OpenAI | ... | A2A     │
       └────────────┬────────────┘
                    │
              Lemonade Router
                    │
          Model/backend manager
                    │
            Local inference
                    │
             CPU / GPU / NPU
```

Start this only after the conformance gate above is closed.

- [ ] Map A2A endpoints onto the current Lemonade C++ HTTP layer.
- [ ] Identify the smallest reusable A2A core independent of Python SDK internals.
- [ ] Define Agent Card generation from Lemonade model/server capabilities.
- [ ] Define Task/Artifact state ownership and lifecycle inside `lemond`.
- [ ] Define streaming bridge from Lemonade generation events to A2A events.
- [ ] Define cancellation from A2A task → Lemonade request/backend execution.
- [ ] Decide whether A2A shares Lemonade's primary port or uses a configurable listener (the sidecar already had to avoid Lemonade's WebSocket port 9000).
- [ ] Align CLI/configuration naming with Lemonade maintainers rather than assuming `--a2a`.
- [ ] Write an upstream design proposal before a large native implementation.

## P2 — Native prototype

Only start this after the conformance, lifecycle and performance gates above are documented.

- [ ] Prototype a minimal native C++ Agent Card endpoint.
- [ ] Prototype native SendMessage → Lemonade Router execution.
- [ ] Add native streaming.
- [ ] Add native cancellation/task lifecycle.
- [ ] Run the same black-box and conformance suite against Python and native implementations.
- [ ] Compare correctness, TTFT, throughput, RSS and CPU overhead.
- [ ] Prepare an upstreamable patch series if maintainers accept the design.

## P3 — Capability expansion

These features are useful only after the core protocol surface is stable:

- [ ] Multi-model Agent Cards and skills based on Lemonade capabilities.
- [ ] Rich A2A parts for supported multimodal Lemonade endpoints.
- [ ] A2A ↔ existing Lemonade API interoperability examples.
- [ ] Capability-aware delegation across multiple local Lemonade nodes.
- [ ] Privacy/locality policy for local and LAN agents.
- [ ] Optional hardware/capability metadata extensions.
- [ ] Quality/latency/energy benchmarking where hardware metrics are available.

## Explicitly out of scope

- [x] Remove CLM/LAYA/JEV or other "System One" model requirements from the architecture.
- [x] Do not build a second model-routing/reasoning framework inside the A2A adapter.
- [x] Do not make AMD hardware a protocol requirement.

If Lemonade gains advanced model routing, scheduling or reasoning features, the A2A surface should reuse them through Lemonade's router instead of duplicating them.

## Recommended execution order

1. ~~Real Lemonade E2E~~ — done (small sample).
2. ~~TTFT measurement~~ — done; extend to larger models and concurrency.
3. **Official A2A TCK/Inspector** — establish protocol evidence. ← next
4. **Lifecycle hardening** — bounded state, backpressure, disconnects, backend-side cancellation proof.
5. **Cross-backend validation** — a second engine and an NPU/ROCm path.
6. **Upstream design proposal** — map the proven behavior into Lemonade's C++ server/router.
7. **Native prototype** — implement only the smallest accepted vertical slice.

This ordering keeps the repository evidence-driven and maximizes the chance that the work can be adopted upstream.
