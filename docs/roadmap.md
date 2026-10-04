# Roadmap / TODO

This roadmap tracks the path from the Python reference adapter to a potential native Lemonade A2A protocol surface.

The architectural rule is simple: **A2A owns agent interoperability; Lemonade owns inference, model management, backend routing and hardware optimization.** The A2A layer must remain usable across local AI systems and must not depend on AMD-specific hardware or a separate reasoning-model stack.

_Last reviewed: 2026-10-04._ A checked box means the item is implemented **and** verified (test, CI, official suite or recorded measurement); caveats are written next to the item.

## Where we are

| Area | Status |
|---|---|
| Protocol surface (Agent Card, JSON-RPC, HTTP+JSON, streaming, task lifecycle, cancellation) | Implemented, unit/E2E tested |
| Official A2A TCK (protocol surface, pinned commit `263b9cf`) | **157 passed, 0 failed**, 4 expected-fail (SHOULD), 104 skipped for undeclared capabilities; scope caveat in [conformance.md](conformance.md) |
| Real Lemonade 2026.40.0 validation | Done: llama.cpp CUDA, Vulkan, CPU; Bonsai-1.7B and Gemma-3-4B; one machine |
| Performance evidence | TTFT overhead roughly 5-15 ms; flat throughput under load (backend-bound); ~70 MB RSS, ~0% idle CPU |
| Cancellation | Verified to stop real backend generation |
| Resource bounds, auth, TLS, shutdown | Implemented (input, deadline, concurrency cap, bounded store, API key, TLS) |
| Independent clients (Go-based A2A CLI, JS SDK, Inspector validators) | Pass on both bindings, mock and real Lemonade; ITK not applicable, .NET/Java/Go libraries not run; see [interoperability.md](interoperability.md) |
| NPU / ROCm / non-llama.cpp engines | **Not measured** (no such hardware on the test machine) |
| Stream backpressure, per-user isolation, rate limiting | **Not implemented** |
| Native Lemonade integration | Not started (by design, see gates below) |

## Done

- [x] Core A2A/Lemonade integration boundary, official `AgentExecutor`, request handler, bounded in-memory task store.
- [x] Lemonade OpenAI-compatible client with SSE parsing and one pooled HTTP client (removed ~350 ms of per-request overhead on Windows).
- [x] Agent Card factory and discovery; hardware-agnostic content enforced by tests; example card checked against the generated card; cache headers; bearer scheme declared only when auth is enforced.
- [x] JSON-RPC and HTTP+JSON at the advertised base URL with legacy `/a2a/*` paths kept; the catch-all `/{tenant}` mount is registered last (a regression here was caught by the TCK and now has tests on both prefixes).
- [x] Streaming bridge, cancellation into the active inference and onto the real backend, `TASK_NOT_CANCELABLE` to HTTP 409, `application/json` REST errors, 415 / `-32005` for non-JSON bodies.
- [x] Input validation and limits: non-text parts rejected; empty/oversized text or too many parts is `InvalidParams`.
- [x] Backend unreachable / timeout / non-2xx ends the task `FAILED` with a sanitized message; per-task deadline; concurrency cap with `REJECTED`; graceful shutdown cancels in-flight inference.
- [x] Optional API-key auth (bearer / `X-API-Key`), backend API key forwarding, TLS (verified with a self-signed certificate).
- [x] Validated `Settings`, version from package metadata, default port 9100 (9000 is Lemonade's WebSocket port), stale duplicate Agent Card builder removed.
- [x] CI: ruff, ruff format check, pytest matrix (3.11-3.13), mock-Lemonade black-box job.
- [x] Official TCK harness (`tck/tck_sut.py`, `scripts/run_tck.py`) and recorded results.
- [x] Benchmark harness (TTFT, latency, throughput, cancellation, footprint, concurrent load) and `cancellation_proof.py`.

## P0 — Conformance evidence

- [x] Run the official A2A TCK against the protocol surface (real server layer + scenario executor) and record commits and results in `conformance-results.json`.
- [x] Fix TCK failures found: route shadowing, Agent Card cache headers, non-JSON content-type handling, Message-vs-Task replies in the test SUT.
- [x] Root-cause the two SHOULD-level deviations `CORE-HIST-005/006`: the TCK reuses one messageId for several messages and the SDK deduplicates it (test artifact, not an adapter defect).
- [x] Independent-client interoperability: A2A CLI v0.3.0 (Go SDK), `@a2a-js/sdk` 1.3.0 and the Inspector validators over JSON-RPC and HTTP+JSON, mock and real Lemonade.
- [ ] ITK: not applicable to a standalone adapter (it tests SDKs against each other through its own agents); revisit only if an adapter-facing mode appears.
- [ ] Client libraries not yet exercised: .NET (preview only), Java, Go library (needs toolchains).
- [ ] Re-test the Go CLI against an auth-declaring card once a2a-go fixes ProtoJSON `securityRequirements` parsing (upstream #430).
- [ ] Report the messageId-reuse issue in the TCK upstream.
- [ ] Add an opt-in CI job that runs the pinned TCK.
- [ ] Decide whether to declare optional capabilities that are currently skipped (extended Agent Card, push notifications, gRPC).

**Exit criterion (met):** TCK clean at MUST level plus independent SDK clients exercising the live adapter.

## P0 — Real Lemonade validation — mostly done

- [x] Validator (`scripts/real_lemonade_e2e.py`): model discovery, Agent Card → `SendMessage` → artifact.
- [x] Real SSE streaming and final artifact assembly.
- [x] Backend-error and timeout mapping (unit-tested; unreachable backend also checked by hand).
- [x] Cancellation stops the real backend request (`benchmarks/cancellation_proof.py`: 30.9 s queued behind a running generation vs 177 ms right after `CancelTask`).
- [ ] Convert the validator into an opt-in `pytest` integration test.
- [ ] Capture service logs and environment metadata automatically on failure.
- [ ] One-command local workflow (start adapter, run validator, run benchmark).
- [ ] Validate with an independent A2A client library, not only the in-repo scripts.

## P0 — Performance evidence

- [x] Direct vs A2A harness: TTFT, total latency, chunks/sec, cancellation, adapter RSS/CPU, median and p95.
- [x] Protocol overhead measured independently of generation time (mock backend: ~+10 ms TTFT).
- [x] Real results on llama.cpp CUDA, Vulkan and CPU with 1.7B and 4B models; 30-run repeats showed single 10-run samples can swing by tens of ms.
- [x] Concurrent load up to N = 8: Lemonade serializes (flat throughput, linear latency growth), the adapter adds no measurable cost, no failures.
- [ ] Longer prompts/outputs and models larger than 4B.
- [ ] More than one machine; more runs per cell (current cells are 10 runs).
- [ ] Define an acceptable overhead budget before native implementation (observed: ~5-15 ms median TTFT).

The objective is not to optimize the Python adapter indefinitely; it is to establish a baseline that tells us whether native Lemonade integration is justified and what it must improve.

## P1 — Lifecycle and resilience

- [x] Active inference cancellation propagation (to the backend, verified).
- [x] Request timeout, per-task deadline, concurrency cap, bounded task store.
- [x] Lemonade unavailable / non-2xx mapped to `FAILED` (model-not-found uses the generic non-2xx message).
- [x] Graceful shutdown cancels in-flight tasks.
- [x] Client disconnect: **decided by design not to cancel** (A2A tasks outlive connections and can be resubscribed); abandoned work is bounded by the deadline and `CancelTask`. Documented in [protocol-mapping.md](protocol-mapping.md).
- [ ] Bounded streaming queues / slow-consumer backpressure.
- [ ] Concurrent task isolation tests (outputs of simultaneous tasks never mix); load tests only show throughput.
- [ ] Repeated cancellation / race-condition tests.
- [ ] Close partial artifacts cleanly when a stream fails mid-way.
- [ ] Opt-in "cancel on disconnect" mode for deployments that prefer it.

## P1 — Security and compatibility hardening

- [x] Smoke conformance in CI and official TCK run.
- [x] Size limits: input characters and parts; task store and concurrency bounds.
- [x] Authentication (shared API key) and TLS options; secrets kept out of `repr`/logs.
- [ ] Per-user identity and task isolation (the store owner is not derived from the key); multiple keys; rate limiting.
- [ ] OAuth/OIDC or mTLS options.
- [x] Request body size limit (413), no public docs/OpenAPI pages, security response headers, startup warnings for exposed deployments.
- [x] Automated security gates in CI: CodeQL, bandit, pip-audit, secret scan, dependency review, weekly schedule, Dependabot; coverage gate at 95% (measured 99%).
- [x] Branch ruleset, CODEOWNERS, PR template, SECURITY.md and agent guard rails written ([governance.md](governance.md)); ruleset applied only after owner confirmation.
- [ ] Fuzz malformed messages, parts and metadata.
- [ ] Validate safe URL/file handling before enabling richer parts.
- [ ] Define local-only, LAN and externally exposed security profiles.
- [x] Publish a formal security policy ([SECURITY.md](../SECURITY.md)).

## P1 — Prove local-AI-system portability

A2A must remain independent of the accelerator selected by Lemonade.

- [x] llama.cpp CUDA, Vulkan and CPU, two models, same adapter and behavior (backend verified from the running `llama-server` path).
- [x] No A2A protocol behavior or Agent Card content depends on AMD-specific metadata (tested).
- [ ] An NPU path (Ryzen AI / FastFlowLM) and an engine other than llama.cpp (ONNX Runtime/OGA); needs hardware this project's test machine lacks.
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

The conformance, interoperability and real-runtime gates are now met; the remaining prerequisite is AMD-hardware (NPU/ROCm) evidence.

- [ ] Map A2A endpoints onto the current Lemonade C++ HTTP layer.
- [ ] Identify the smallest reusable A2A core independent of Python SDK internals.
- [ ] Define Agent Card generation from Lemonade model/server capabilities.
- [ ] Define Task/Artifact state ownership and lifecycle inside `lemond`.
- [ ] Define streaming bridge from Lemonade generation events to A2A events.
- [ ] Define cancellation from A2A task → Lemonade request/backend execution (the Python reference now proves the behavior to match).
- [ ] Decide whether A2A shares Lemonade's primary port or uses a configurable listener (the sidecar already had to avoid Lemonade's WebSocket port 9000).
- [ ] Align CLI/configuration naming with Lemonade maintainers rather than assuming `--a2a`.
- [ ] Write an upstream design proposal before a large native implementation.

## P2 — Native prototype

Only start this after the proposal is accepted in principle.

- [ ] Prototype a minimal native C++ Agent Card endpoint.
- [ ] Prototype native SendMessage → Lemonade Router execution.
- [ ] Add native streaming.
- [ ] Add native cancellation/task lifecycle.
- [ ] Run the same black-box and TCK suites against Python and native implementations.
- [ ] Compare correctness, TTFT, throughput, RSS and CPU overhead against the recorded baselines.
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

1. ~~Real Lemonade E2E~~ — done (CUDA, Vulkan, CPU; 1.7B and 4B).
2. ~~TTFT, load and cancellation evidence~~ — done on one machine.
3. ~~Official A2A TCK~~ — done for the protocol surface; two SHOULD deviations open.
4. ~~Independent-client interoperability~~ — done for the CLI, JS SDK and Inspector validators.
5. **AMD hardware evidence** ← next — NPU/ROCm and a non-llama.cpp engine, which needs access to such a machine.
6. **Remaining hardening** — backpressure, per-user isolation, rate limiting, fuzzing.
7. **Upstream design proposal**, then a **native prototype** of the smallest accepted vertical slice.

This ordering keeps the repository evidence-driven and maximizes the chance that the work can be adopted upstream.
