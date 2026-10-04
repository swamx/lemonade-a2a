# Roadmap / TODO

This roadmap tracks the path from the Python reference adapter to a potential native Lemonade A2A protocol surface.

The architectural rule is simple: **A2A owns agent interoperability; Lemonade owns inference, model management, backend routing and hardware optimization.** The A2A layer must remain usable across local AI systems and must not depend on AMD-specific hardware or a separate reasoning-model stack.

## Current state

- [x] Core A2A/Lemonade integration boundary defined.
- [x] Lemonade OpenAI-compatible client implemented.
- [x] Agent Card factory and discovery implemented.
- [x] Official A2A `AgentExecutor` binding implemented.
- [x] Request handler and in-memory task store implemented.
- [x] Text message → Lemonade inference mapping implemented.
- [x] A2A artifacts/task lifecycle implemented.
- [x] Health/readiness surface implemented.
- [x] Lemonade SSE parser implemented.
- [x] Streaming event bridge implemented.
- [x] Deterministic Mock Lemonade E2E test implemented.
- [x] Full black-box adapter stack exercised in CI.
- [x] Cancellation propagated into the active inference coroutine.
- [x] Python CI matrix established.
- [x] A2A conformance workflow established.
- [x] Direct-vs-A2A latency benchmark tooling implemented.
- [x] CI and E2E green on current `main` after the latest cancellation/benchmark validation changes.

## P0 — Real Lemonade validation — NEXT

This is the highest-value next milestone. The deterministic mock proves our adapter behavior; now prove the integration against Lemonade itself.

- [ ] Add an opt-in integration test against a real Lemonade Server on port 13305.
- [ ] Discover/select a real installed Lemonade model rather than hard-coding one.
- [ ] Validate Agent Card → SendMessage → Lemonade → A2A artifact end-to-end.
- [ ] Validate real SSE streaming and final artifact assembly.
- [ ] Validate cancellation closes/stops the active real backend request.
- [ ] Validate timeout and backend-error mapping.
- [ ] Capture service logs and reproducible environment metadata on failure.
- [ ] Document a one-command local integration-test workflow.

**Exit criterion:** an official A2A client can discover the adapter, invoke a real Lemonade-hosted model, stream output, cancel work, and observe correct task states.

## P0 — Performance evidence

- [x] Direct Lemonade vs A2A total-latency benchmark harness.
- [x] Measure time-to-first-token (TTFT) for direct vs A2A streaming (harness done; mock-backed protocol-overhead run recorded in `docs/benchmarks.md`; real-model runs still pending).
- [ ] Measure A2A protocol overhead independently from model generation time.
- [ ] Report median and p95 across repeated runs.
- [ ] Measure idle RSS/CPU of the Python reference adapter.
- [ ] Define an acceptable overhead budget before native implementation.

The objective is not to optimize the Python adapter indefinitely. It is to establish a baseline that tells us whether native Lemonade integration is justified and what it must improve.

## P1 — Lifecycle and resilience

- [x] Active inference cancellation propagation.
- [ ] Bounded streaming queues/backpressure.
- [ ] Client disconnect propagation.
- [ ] Request timeout policy.
- [ ] Lemonade unavailable/model unavailable mapping.
- [ ] Concurrent task isolation tests.
- [ ] Repeated cancellation/race-condition tests.
- [ ] Graceful server shutdown with active tasks.

## P1 — A2A compatibility and security

- [x] Automated conformance checks in CI.
- [ ] Run official A2A Inspector/TCK against the live adapter.
- [ ] Publish the exact compatibility pass/fail matrix.
- [ ] Fuzz malformed messages, parts and metadata.
- [ ] Add message/artifact/request-size limits.
- [ ] Validate safe URL/file handling before enabling richer parts.
- [ ] Define local-only, LAN and externally exposed security profiles.
- [ ] Add authentication/TLS guidance for non-loopback deployments.

## P1 — Prove local-AI-system portability

A2A must remain independent of the accelerator selected by Lemonade.

- [ ] Validate on at least two materially different Lemonade backend configurations.
- [ ] Include a CPU or generic backend validation path where practical.
- [ ] Include an accelerated GPU/NPU backend validation path where practical.
- [ ] Verify no A2A protocol behavior depends on AMD-specific metadata.
- [ ] Keep optional hardware metadata informational and extension-based.
- [ ] Use **local AI system** terminology in generic architecture documentation.
- [ ] Reserve AMD-specific language for AMD challenge/optimization material where it is actually relevant.

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

- [ ] Map A2A endpoints onto the current Lemonade C++ HTTP layer.
- [ ] Identify the smallest reusable A2A core independent of Python SDK internals.
- [ ] Define Agent Card generation from Lemonade model/server capabilities.
- [ ] Define Task/Artifact state ownership and lifecycle inside `lemond`.
- [ ] Define streaming bridge from Lemonade generation events to A2A events.
- [ ] Define cancellation from A2A task → Lemonade request/backend execution.
- [ ] Decide whether A2A shares Lemonade's primary port or uses a configurable listener.
- [ ] Align CLI/configuration naming with Lemonade maintainers rather than assuming `--a2a`.
- [ ] Write an upstream design proposal before a large native implementation.

## P2 — Native prototype

Only start this after the real-runtime, compatibility and performance gates above are documented.

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

1. **Real Lemonade E2E** — prove the reference implementation against actual Lemonade.
2. **TTFT + lifecycle hardening** — quantify overhead and close cancellation/timeout/backpressure gaps.
3. **Official A2A TCK/Inspector** — establish protocol evidence.
4. **Cross-backend validation** — prove that the architecture is for local AI systems, not one accelerator.
5. **Upstream design proposal** — map the proven behavior into Lemonade's C++ server/router.
6. **Native prototype** — implement only the smallest accepted vertical slice.

This ordering keeps the repository evidence-driven and maximizes the chance that the work can be adopted upstream.
