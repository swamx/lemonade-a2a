# Roadmap

## Phase 0 — Standards baseline

- [x] Define A2A/Lemonade integration boundary.
- [x] Add dependency-light Lemonade client.
- [x] Add Agent Card factory.
- [ ] Pin a tested A2A SDK release/API surface.
- [ ] Add CI matrix for supported Python versions.
- [ ] Record A2A Inspector/TCK baseline.

## Phase 1 — Working MVP

- [ ] Implement official A2A `AgentExecutor` binding.
- [ ] Implement request handler/task store.
- [ ] Map text messages to Lemonade chat completions.
- [ ] Return standards-compliant A2A response messages.
- [ ] Add health/readiness endpoints.
- [ ] End-to-end test with a real Lemonade server.

**Exit criterion:** an official A2A client can discover the Agent Card, send a text task, receive locally generated Lemonade output, and observe correct task states.

## Phase 2 — Streaming and lifecycle

- [x] Lemonade SSE parser.
- [x] A2A streaming event bridge.
- [ ] cancellation propagation.
- [ ] bounded queues/backpressure.
- [ ] timeout and failure mapping.

## Phase 3 — Compatibility and hardening

- [ ] Run official A2A compatibility tooling.
- [ ] Publish exact pass/fail matrix.
- [ ] fuzz malformed messages/parts.
- [ ] concurrency/load tests.
- [ ] security limits and safe defaults.
- [ ] package/release automation.

## Phase 4 — Lemonade-native proposal

- [ ] Compare sidecar vs native server integration.
- [ ] Draft upstream design proposal.
- [ ] Align CLI/configuration with Lemonade maintainers.
- [ ] Prototype `--a2a` or equivalent native surface.
- [ ] Contribute integration upstream if accepted.

## Phase 5 — Experimental intelligence layer

These features remain optional and must not compromise A2A compliance:

- [ ] multi-model skills;
- [ ] A2A ↔ OpenAI bridge;
- [ ] CLM/LAYA System-One decision routing;
- [ ] capability-aware agent/model scheduler;
- [ ] speculative delegation;
- [ ] privacy/locality constraints;
- [ ] AMD hardware metadata extensions;
- [ ] quality-per-joule benchmarking.

## Performance targets

The initial Python implementation optimizes correctness and standards validation. A later Rust/Go sidecar can evaluate:

- near-zero idle CPU;
- sub-100 ms startup;
- very small resident memory;
- minimal added first-token latency.

`<10 MB RAM` remains an experimental target until reproducible measurements demonstrate it.
