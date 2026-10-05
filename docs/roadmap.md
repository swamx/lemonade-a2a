# Roadmap / TODO

This roadmap tracks the path from the Python reference adapter to a potential native Lemonade A2A protocol surface.

The architectural rule is simple: **A2A owns agent interoperability; Lemonade owns inference, model management, backend routing and hardware optimization.** The A2A layer must remain usable across local AI systems and must not depend on AMD-specific hardware or a separate reasoning-model stack.

_Last reviewed: 2026-10-05._ A checked box means the item is implemented **and** verified (test, CI, official suite or recorded measurement); caveats are written next to the item.

## Where we are

| Area | Status |
|---|---|
| Protocol surface (Agent Card, JSON-RPC, HTTP+JSON, streaming, task lifecycle, cancellation) | Implemented, unit/E2E tested |
| Official A2A TCK (protocol surface, pinned commit `263b9cf`) | **157 passed, 0 failed**, 4 expected-fail (SHOULD); with the messageId workaround for [a2a-tck#248](https://github.com/a2aproject/a2a-tck/issues/248) **161 passed, 0 deviations**; both enforced in CI; scope caveat in [conformance.md](conformance.md) |
| Real Lemonade 2026.40.0 validation | Done: llama.cpp CUDA, Vulkan, CPU; Bonsai-1.7B and Gemma-3-4B; one machine |
| Performance evidence | TTFT overhead roughly 5-15 ms; flat throughput under load (backend-bound); ~70 MB RSS, ~0% idle CPU |
| Cancellation | Verified to stop real backend generation |
| Resource bounds, auth, TLS, shutdown | Implemented (input, deadline, concurrency cap, bounded store, backpressure, named API keys with per-user isolation, rate limiting, TLS and mutual TLS, exposure profiles, opt-in cancel on disconnect) |
| Fuzzing | Property-based; found and fixed two defects (non-UTF-8 bodies, wrongly shaped backend stream events) |
| Independent clients (A2A CLI, JS, Go, .NET and Java SDKs, Inspector validators) | Pass on both bindings, mock and real Lemonade; ITK not applicable; see [interoperability.md](interoperability.md) |
| NPU / ROCm / Metal / non-llama.cpp engines | **Not measured** (no such hardware on the test machine); the only open P1 items |
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
- [x] ITK: **decided not applicable** to a standalone adapter (it tests SDKs against each other through its own agents); revisit only if an adapter-facing mode appears.
- [x] Client libraries exercised: Go library (a2a-go v2.6.0), .NET (`A2A` 1.0.0-preview2) and Java (1.4.0.Final), each 10/10 over both bindings with API-key auth (`interop/`).
- [x] Re-test the Go CLI against an auth-declaring card: fixed in a2a-go v2.6.0; a CLI rebuilt on it parses the card and sends on both bindings (upstream #430).
- [x] Report the messageId-reuse issue in the TCK upstream: filed as [a2aproject/a2a-tck#248](https://github.com/a2aproject/a2a-tck/issues/248) (text kept in [upstream-issues/tck-message-id-reuse.md](upstream-issues/tck-message-id-reuse.md)); a workaround run is enforced in CI until it is fixed.
- [x] Add an opt-in CI job that runs the pinned TCK (`.github/workflows/tck.yml`; first GitHub run is on the PR that adds it).
- [x] Decide whether to declare optional capabilities that are currently skipped: **no**, with reasons and revisit triggers in [conformance.md](conformance.md).

**Exit criterion (met):** TCK clean at MUST level plus independent SDK clients exercising the live adapter. The upstream issue is filed and the TCK job runs in CI (official and workaround runs).

## P0 — Real Lemonade validation — done

- [x] Validator (`scripts/real_lemonade_e2e.py`): model discovery, Agent Card → `SendMessage` → artifact.
- [x] Real SSE streaming and final artifact assembly.
- [x] Backend-error and timeout mapping (unit-tested; unreachable backend also checked by hand).
- [x] Cancellation stops the real backend request (`benchmarks/cancellation_proof.py`: 30.9 s queued behind a running generation vs 177 ms right after `CancelTask`).
- [x] Opt-in `pytest` integration tests (`tests/integration/`, marker `integration`, skipped unless a real Lemonade is configured): 6/6 pass locally.
- [x] Service logs and environment metadata captured automatically on failure (`src/lemonade_a2a/diagnostics.py`, written to `integration-diagnostics/`); verified with a deliberately failing test.
- [x] One-command local workflow: `python scripts/validate_real_lemonade.py` starts the adapter, runs the validator, pytest suite and a benchmark; all four steps pass.
- [x] Validated with independent client libraries (JS, Go, .NET, Java, A2A CLI) against real Lemonade; see [interoperability.md](interoperability.md).

## P0 — Performance evidence

- [x] Direct vs A2A harness: TTFT, total latency, chunks/sec, cancellation, adapter RSS/CPU, median and p95.
- [x] Protocol overhead measured independently of generation time (mock backend: ~+10 ms TTFT).
- [x] Real results on llama.cpp CUDA, Vulkan and CPU with 1.7B and 4B models; 30-run repeats showed single 10-run samples can swing by tens of ms.
- [x] Concurrent load up to N = 8: Lemonade serializes (flat throughput, linear latency growth), the adapter adds no measurable cost, no failures.
- [~] Longer prompts/outputs and models larger than 4B: done for 1.7B and 4B on three backends; Gemma-4-12B only partly measured (CUDA short only; long workloads failed in the direct path, CPU cell hung). See [benchmarks.md](benchmarks.md).
- [~] More runs per cell: done (30 for short cells). More than one machine: only through the `bench.yml` GitHub-runner job, which has not run yet.
- [~] Overhead budget defined and enforced by `benchmarks/check_budget.py` (see [benchmarks.md](benchmarks.md)). Every 30-run cell on the 1.7B/4B models is within it; Gemma-4-12B on CUDA is **over** (+1.6 s, likely noise on a memory-bound GPU, unconfirmed).

The objective is not to optimize the Python adapter indefinitely; it is to establish a baseline that tells us whether native Lemonade integration is justified and what it must improve.

## P1 — Lifecycle and resilience

- [x] Active inference cancellation propagation (to the backend, verified).
- [x] Request timeout, per-task deadline, concurrency cap, bounded task store.
- [x] Lemonade unavailable / non-2xx mapped to `FAILED` (model-not-found uses the generic non-2xx message).
- [x] Graceful shutdown cancels in-flight tasks.
- [x] Client disconnect: **decided by design not to cancel** (A2A tasks outlive connections and can be resubscribed); abandoned work is bounded by the deadline and `CancelTask`. Documented in [protocol-mapping.md](protocol-mapping.md).
- [x] Bounded streaming queues / slow-consumer backpressure: provided by the A2A SDK's bounded event queue and **verified end to end** against a stalled client (the adapter keeps serving, memory stays flat, the task ends `FAILED` at its deadline). The bound (1,024 events) is the SDK's and not configurable here; a configurable bound would need an SDK change.
- [x] Concurrent task isolation tests: 40 simultaneous jittery streams each return exactly their own prompt, and the executor's bookkeeping is empty afterwards (`tests/test_stream_failures_and_isolation.py`).
- [x] Repeated cancellation / race-condition tests: 24 start-then-cancel races at staggered delays, each cancelled twice, over real sockets; every task ends terminal, none stays running, no 5xx (`tests/test_live_lifecycle.py`).
- [x] Close partial artifacts cleanly when a stream fails mid-way: the artifact is closed with `last_chunk=true` before `FAILED` (backend error, timeout, unreadable stream, deadline). A *cancelled* stream is left as streamed; the cancel path publishes the terminal status itself.
- [x] Opt-in "cancel on disconnect" mode (`LEMONADE_A2A_CANCEL_ON_DISCONNECT=1`): cancels the task started by a streaming request when its client goes away; a resubscriber leaving does not (tested over real sockets).

## P1 — Security and compatibility hardening

- [x] Smoke conformance in CI and official TCK run.
- [x] Size limits: input characters and parts; task store and concurrency bounds.
- [x] Authentication (shared API key) and TLS options; secrets kept out of `repr`/logs.
- [x] Per-user identity and task isolation (the key's name is the task owner), multiple named keys, per-identity rate limiting (429 + `Retry-After`); tested on both bindings, including cross-user get, list, cancel and continue.
- [x] mTLS option (verified with generated certificates: valid, missing and foreign-CA clients). OAuth/OIDC is **decided out of the adapter** and documented as a gateway pattern in [security.md](security.md); revisit only if the adapter must read the token subject itself.
- [x] Request body size limit (413), no public docs/OpenAPI pages, security response headers, startup warnings for exposed deployments.
- [x] Automated security gates in CI: CodeQL, bandit, pip-audit, secret scan, dependency review, weekly schedule, Dependabot; coverage gate at 95% (measured 99%).
- [x] Branch ruleset, CODEOWNERS, PR template, SECURITY.md and agent guard rails written ([governance.md](governance.md)); ruleset applied only after owner confirmation.
- [x] Fuzz malformed messages, parts and metadata (`tests/test_fuzz.py`, property-based, REST and JSON-RPC, raw bytes, task queries, backend stream lines). It found and fixed two defects: non-UTF-8 bodies returned an internal error, and wrongly shaped backend stream events crashed the stream parser.
- [x] Validate safe URL/file handling before enabling richer parts: file, URL and data parts are rejected on both bindings (tested with `file:` and cloud-metadata URLs); the SSRF policy that must gate any future fetch is written and tested first (`safe_urls.py`, 50 cases).
- [x] Define local-only, LAN and externally exposed security profiles (`LEMONADE_A2A_PROFILE`): unsafe combinations refuse to start; table in [security.md](security.md).
- [x] Publish a formal security policy ([SECURITY.md](../SECURITY.md)).

## P1 — Prove local-AI-system portability

A2A must remain independent of the accelerator selected by Lemonade.

- [x] llama.cpp CUDA, Vulkan and CPU, two models, same adapter and behavior (backend verified from the running `llama-server` path).
- [x] No A2A protocol behavior or Agent Card content depends on AMD-specific metadata (tested).
- [ ] An NPU path (Ryzen AI / FastFlowLM) and an engine other than llama.cpp (ONNX Runtime/OGA). **Blocked on hardware** this project's test machine lacks; the adapter has no hardware-specific code, so the check is to run the existing validator and benchmark on such a machine.
- [ ] ROCm and Metal paths where hardware allows. **Blocked on hardware** (same reason).
- [x] Keep optional hardware metadata informational and extension-based: rule written in [architecture.md](architecture.md) and enforced by tests (no accelerator or vendor term in the Agent Card in any configuration, identical card whatever backend runs, any extension must be optional).
- [x] Use **local AI system** terminology in generic documentation; AMD-specific language only in [amd-challenge.md](amd-challenge.md) (a test fails if architecture, protocol, security or conformance docs name AMD, Ryzen or ROCm).

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
3. ~~Official A2A TCK~~ — done for the protocol surface; the two SHOULD deviations are a TCK test artifact, reported upstream (#248) and covered by a workaround run.
4. ~~Independent-client interoperability~~ — done for the CLI and the JS, Go, .NET and Java SDKs plus the Inspector validators.
5. ~~Remaining hardening~~ — done: backpressure, per-user isolation, rate limiting, mTLS, profiles, fuzzing.
6. **AMD hardware evidence** ← next — NPU/ROCm/Metal and a non-llama.cpp engine, which needs access to such a machine.
7. **Upstream design proposal**, then a **native prototype** of the smallest accepted vertical slice.

This ordering keeps the repository evidence-driven and maximizes the chance that the work can be adopted upstream.
