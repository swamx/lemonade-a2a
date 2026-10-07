# Roadmap / TODO

This roadmap tracks the path from the Python reference adapter to a potential native Lemonade A2A protocol surface.

The architectural rule is simple: **A2A owns agent interoperability; Lemonade owns inference, model management, backend routing and hardware optimization.** The A2A layer must remain usable across local AI systems and must not depend on AMD-specific hardware or a separate reasoning-model stack.

_Last reviewed: 2026-10-05._ A checked box means the item is implemented **and** verified (test, CI, official suite or recorded measurement); caveats are written next to the item.

**Tiers.** P0 and P1 are complete (their open remainders moved to P2). **P2** is the carry-over from P0/P1 plus two new workstreams: a plug-and-play compatibility specification and OpenTelemetry observability. **P3** is the Lemonade-native proposal and prototype. **P4** is capability expansion.

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
| NPU / ROCm / Metal / non-llama.cpp engines | **Not measured** (no such hardware on the test machine); tracked in P2 carry-over |
| Compatibility specification, `doctor`, feature registry, extension API | **Designed** ([specification.md](specification.md)); not implemented (P2) |
| OpenTelemetry observability | **Designed** ([observability.md](observability.md)); not implemented; the A2A SDK already emits its own spans (P2) |
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
- [x] Longer prompts/outputs on the 1.7B and 4B models across three backends. Gemma-4-12B is only partly measured (CUDA short only); the remainder moved to **P2 carry-over** (cause diagnosed: reasoning model). See [benchmarks.md](benchmarks.md).
- [x] More runs per cell (30 for short cells) and a second machine for protocol overhead (the `bench.yml` Linux runner, mock backend: +5.5 ms TTFT). Real inference on a second machine moved to **P2 carry-over**.
- [x] Overhead budget defined and enforced by `benchmarks/check_budget.py` (see [benchmarks.md](benchmarks.md)). Every 30-run cell on the 1.7B/4B models is within it; Gemma-4-12B on CUDA is **over** (+1.6 s, likely noise, unconfirmed): confirming it moved to **P2 carry-over**.

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
- [ ] → **moved to P2 carry-over** (blocked on hardware): an NPU path (Ryzen AI / FastFlowLM) and an engine other than llama.cpp (ONNX Runtime/OGA).
- [ ] → **moved to P2 carry-over** (blocked on hardware): ROCm and Metal paths.
- [x] Keep optional hardware metadata informational and extension-based: rule written in [architecture.md](architecture.md) and enforced by tests (no accelerator or vendor term in the Agent Card in any configuration, identical card whatever backend runs, any extension must be optional).
- [x] Use **local AI system** terminology in generic documentation; AMD-specific language only in validation records and benchmarks (a test fails if architecture, protocol, security or conformance docs name AMD, Ryzen or ROCm).

## P2 — Carry-over from P0 and P1

Everything P0 and P1 left open, in one place. Nothing here blocks the specification or observability work below, except where noted.

**Evidence**

- [x] **Gemma-4-12B and other reasoning models**: re-run and **resolved** ([benchmarks.md](benchmarks.md#gemma-4-12b-re-run-2026-10-06)). The earlier "no text chunks" failures were *not* reasoning: the model loads with a 1,459-token context and Lemonade reported the oversized prompt as an error inside a 200 stream, which also exposed and fixed an adapter defect (such a task used to complete empty). The harness now explains empty streams, a `medium-prompt` workload fits the small context, and the harness records time to the first output of any kind plus reasoning chunks per run, because answer-chunk TTFT is dominated by the model's random thinking length. Results: 30-run short +87 ms TTFT, 20-run medium prompt +7 ms to first output / +320 ms to first answer, CPU (4 runs, indicative) +4.4 s on a 53-72 s spread; all within budget except where marked indicative. The long-prompt workload cannot run on this GPU for this model.
- [x] **Confirm or refute the +1.6 s overhead** seen for 12B on CUDA: **refuted as noise.** A 30-run repeat measured +87 ms TTFT and +496 ms total latency (limit 1.2 s, 5% of a 24 s request), throughput identical (16.8 vs 16.9 chunks/s): within budget. The direct runs alone varied from 14 s to 37 s to the first token. See [benchmarks.md](benchmarks.md#gemma-4-12b-re-run-2026-10-06).
- [ ] **Real inference on a second machine.** The Linux runner measures protocol overhead against the mock only. Run `validate_real_lemonade.py` and the benchmarks on another PC (or a self-hosted runner with a small CPU model).
- [x] **Decide how the adapter treats `reasoning_content`**: dropped by default, optionally streamed as a separate `lemonade-reasoning` artifact (`LEMONADE_A2A_REASONING`); a response that is all reasoning fails with a clear message instead of completing empty. Tested, with recorded vLLM-style reasoning streams ([protocol-mapping.md](protocol-mapping.md)).

**Hardware (blocked: this test machine has an Intel CPU and an NVIDIA GPU)**

- [ ] NPU path (Ryzen AI / FastFlowLM) and an engine other than llama.cpp (ONNX Runtime/OGA). The adapter has no hardware-specific code, so the check is to run the existing validator, live tests and benchmark on such a machine.
- [ ] ROCm and Metal paths.

**Deferred decisions, each with its revisit trigger**

- [~] Configurable event-queue bound: needs an upstream change in the A2A SDK (its v2 handler ignores a custom queue manager). **Drafted** in [upstream-issues/a2a-sdk-event-queue-bound.md](upstream-issues/a2a-sdk-event-queue-bound.md) with the stalled-client evidence; **not filed**, because filing an issue on another project needs the owner's OK. Revisit when the SDK exposes it.
- [x] Read the OAuth/OIDC token subject inside the adapter: **closed as a decision.** The gateway pattern is documented ([security.md](security.md)) and the extension API now gives a deployment that needs it a supported way to do so (an authenticator plugin returns the identity, which becomes the task owner) without changing the adapter. Reopen if such a plugin should ship in the box.
- [x] ITK: **closed as a decision**: it tests SDKs against each other through its own agents, so it does not apply to a standalone adapter. Revisit only if an adapter-facing mode appears.
- [ ] Re-test the A2A CLI with card discovery once a CLI release bundles a2a-go v2.6.0 or later. **Checked 2026-10-06:** the latest CLI release is still v0.3.0 and `main` still depends on a2a-go v2.5.0, so there is nothing to re-test yet (a CLI rebuilt on v2.6.0 already passes; see [interoperability.md](interoperability.md)). External dependency.
- [ ] Remove the TCK messageId workaround and the two expected deviations when [a2a-tck#248](https://github.com/a2aproject/a2a-tck/issues/248) is fixed. **Checked 2026-10-06:** the issue is open with no response. Nothing to do until then; the patch will stop applying when the TCK changes, which fails the CI job and is the signal. External dependency.
- [x] Re-evaluate the optional capabilities left undeclared (gRPC, push notifications, extended Agent Card): **done**, decisions unchanged, with reasons and triggers in [conformance.md](conformance.md#optional-capabilities-re-evaluated-2026-10-06).

**Exit criterion:** every item is either done with evidence or explicitly closed with a reason; the hardware items may remain open only as "blocked on hardware".

## P2 — Plug-and-play compatibility specification (new)

Design: [specification.md](specification.md). The goal is that after `pip install -U a2a-sdk lemonade-a2a` a single command cross-validates support and features, with control over how strictly the adapter reacts, flexibility to swap parts, and a support bundle that answers a bug report.

**Specification and data**

- [x] Review and accept the draft specification (open questions in §11 resolved or deferred). **Done**: accepted and implemented; the open questions are answered in [specification.md](specification.md#11-decisions-taken-formerly-open-questions).
- [x] Feature registry (`features.json` plus JSON Schema) seeded from the current state, with `verified_by` evidence for every `supported` entry. **Done**: 49 features, schema-validated, evidence for every `supported` entry checked by `tests/test_registry.py`; rendered to [features.md](features.md).
- [x] `@pytest.mark.feature("<id>")` marker and a CI check that the registry and the tests agree (no `supported` feature without a passing test, no unknown ids). **Done differently**: no marker was needed; the registry names its evidence and `tests/test_registry.py` fails if a named test, script or TCK result does not exist, a supported feature has none, a setting is unknown, or `features.md` is stale.
- [x] Compatibility manifest (`compat.json`) generated from CI results at release time, never hand-edited. **Done**: `scripts/generate_manifest.py` writes it from canary and TCK results; `doctor` warns if it was generated for another adapter version.

**Tooling**

- [x] `lemonade-a2a doctor`: component versions against the manifest, config consistency, card versus registry, backend probe; verdicts and exit codes `0/1/2/3`; `--json`, `--strict`. **Done**: `tests/test_cli_and_compat.py`; also checks plugins and the Lemonade model's context window.
- [x] `lemonade-a2a capabilities`: the registry resolved for this install and configuration. **Done**.
- [x] `lemonade-a2a doctor --sdk-gap`: what the installed `a2a-sdk` offers that the adapter does not use. **Done** (`src/lemonade_a2a/sdk_gap.py`).
- [x] `lemonade-a2a config show | validate | schema` with secrets redacted and the source of each value. **Done**; also `config schema` as JSON Schema.
- [x] `lemonade-a2a support-bundle`: redacted archive (versions, effective config, doctor output, logs, platform and Lemonade info), tested for the absence of prompts, responses and keys; issue template asks for it. **Done**: redaction of keys and the home directory is tested; GitHub issue templates ask for `doctor` output and the bundle.
- [x] Optional `GET /.well-known/lemonade-a2a/capabilities` (authenticated, off by default) and an optional, never-required Agent Card extension pointing to it. **Done**, including the test that the `/{tenant}` mount does not shadow it.

**Control and flexibility**

- [x] `LEMONADE_A2A_COMPAT=off|warn|strict` evaluated at startup with the same checks as `doctor`. **Done**; the result is also the `lemonade_a2a_compat_status` metric.
- [x] Feature flags by registry id for opt-in and experimental features; turning a core feature off changes both the Agent Card and the behaviour, checked by `doctor`. **Done**: `LEMONADE_A2A_FEATURES`; `-a2a.streaming` makes the card say `streaming: false` and the streaming methods answer `UNSUPPORTED_OPERATION` on both bindings (tested).
- [x] Layered configuration (defaults, config file, environment, flags); every existing environment variable keeps working. **Done**: TOML file, environment, flags; `config show` reports the source of every value.
- [x] Extension API v1 through entry points: `lemonade_a2a.backends`, `task_stores`, `authenticators`, `telemetry`, each a small `Protocol` with an `api_version`; `doctor` lists plugins and flags mismatches. **Done** ([extending.md](extending.md)); `doctor` lists plugins and fails one built for another API version.
- [x] A reference second implementation per seam to prove the API is usable: a persistent (SQLite) task store, and a second OpenAI-compatible backend in the contract tests. **Done**: SQLite task store (reference), a second backend, an authenticator and a telemetry plugin running real requests in tests, and OpenAI / llama.cpp / vLLM stream shapes in the contract tests.

**Upgrade safety**

- [x] Canary CI matrix: lowest supported, latest stable and latest pre-release `a2a-sdk`, running unit tests, the TCK (official and patched) and `doctor`; failure on *latest* opens an issue, failure on *lowest* blocks merges. **Done**: [canary.yml](../.github/workflows/canary.yml) (lowest blocks, latest opens an issue, unbounded is informational), each running tests, `doctor` and the TCK twice. The first GitHub run happens on the pull request that adds it.
- [x] Backend contract tests (`tests/contract/`): recorded request and stream fixtures per Lemonade version; a new Lemonade release is added by recording fixtures. **Done**: `tests/contract/` with recorded Lemonade streams and hand-authored OpenAI / llama.cpp / vLLM streams; a tested version without recordings fails a test.
- [x] Widen or confirm the `a2a-sdk` range (`>=1.2.0,<1.3`) based on the canary instead of by guess: **confirmed.** The first local canary run (2026-10-07) installed a clean environment per version and ran the whole suite, `doctor` and the TCK twice: **1.2.0, 1.2.1 and 1.2.2 all pass** (422 tests each; TCK 157 passed with only the two known deviations, 161 passed with the workaround). The canary also found a real flaw on its first run: five of *my own* tests assumed the shipped manifest lists whichever SDK is installed, so they failed on 1.2.0 and 1.2.2 even though the product was right (`doctor` correctly warned "inside the range but untested"); they now use a manifest fixture, and the shipped manifest is generated from this run (`tested`: 1.2.0, 1.2.1, 1.2.2). Widening past 1.2.x is deferred: there is no 1.3 release to test, and the `unbounded` variant will say what it breaks when one appears.
- [x] Version and deprecation policy written down (semantic versioning, deprecation period for settings and the extension API). **Done**: [upgrading.md](upgrading.md#versioning-policy).
- [x] Packaging: wheel and sdist build in CI, optional extras (`[otel]`, `[dev]`), and a release process. **Publishing to PyPI needs the owner's explicit approval and is not part of this item.** **Done**: the `package` job builds, installs the wheel alone with extras into a clean environment and runs the user commands; [releasing.md](releasing.md) is the checklist. **Publishing to PyPI still needs the owner's explicit approval and was not done.**

**Exit criterion:** on a clean environment, `pip install -U a2a-sdk lemonade-a2a` followed by `lemonade-a2a doctor` gives a correct verdict in under five seconds for a supported combination, a clear failure for a known-broken one, and the canary matrix has run green at least once on all three SDK variants.

## P2 — Observability with OpenTelemetry (new)

Design: [observability.md](observability.md). Opt-in, free when off, private by default, never able to hurt a request.

**Foundation**

- [x] Review and accept the draft (semantic-convention version to pin, open questions in §9). **Done**: GenAI conventions followed as of this release and flagged as in development upstream; open questions answered in [observability.md](observability.md).
- [x] `lemonade_a2a.telemetry` facade over `opentelemetry-api` (no-op when off); `[otel]` extra for the SDK and OTLP exporters; declare the API dependency explicitly. **Done**; `opentelemetry-api` is now a declared core dependency.
- [x] Configuration: standard `OTEL_*` variables plus `LEMONADE_A2A_OTEL`, `_CAPTURE`, `_REDACT`, `_PROMETHEUS`, `_BUFFER`; validated by `config validate`; reported by `doctor`. **Done**; validated by `config validate`, reported by `doctor`.

**Signals**

- [x] Traces: request span, `a2a.task.execute` (with first-chunk, cancel, disconnect, deadline and stall events), `lemonade.chat.stream` client span; span links for later requests about a task; W3C trace-context in and out (to Lemonade); sanitized error recording. **Done** (a `backpressure_stall` event was dropped: the SDK handles the stall and there is nothing in the adapter to observe).
- [x] Metrics: the instrument table in [observability.md](observability.md), low cardinality only; GenAI conventions behind the pinned version. **Done**; the adapter is a GenAI client of Lemonade, so it records `gen_ai.client.*`.
- [x] Logs: structured JSON with trace and span ids, stable event names, optional OpenTelemetry log bridge. **Done**.
- [x] Optional Prometheus pull endpoint (authenticated). **Done**.

**Privacy, safety, performance**

- [x] Capture modes `none` (default) / `metadata` / `content`; the `external` profile refuses `content` and plaintext OTLP without explicit acceptance. **Done**; refusal under `external` is tested.
- [x] Property test: random prompts never appear in any exported span, metric or log in `none` and `metadata` modes. **Done** (hypothesis, success and failure paths).
- [x] Resilience tests: collector down or slow and exporter errors never fail or slow requests; bounded buffers; shutdown flush is bounded. **Done**: dead, raising and slow exporters, bounded shutdown.
- [x] Benchmark modes and a telemetry overhead budget: `benchmarks/telemetry_overhead.py` (paired, randomized order, with an identical control process); instrumentation +1.1 ms, 10% sampling +0.5 ms, full OTLP export +1.9 ms on a 40 ms mock request, throughput unchanged. The budget was **revised from 1 ms to 2.5 ms (or 5%)** after measuring, with the reasons written down ([observability.md](observability.md#6-performance)). The same measurement found that the A2A SDK's own tracing cost more than ours with telemetry *off* (+15.6 ms bursty, 2-5 ms warm); `serve` now turns it off by default.

**Operations**

- [x] `examples/observability/`: Docker Compose stack (Collector, trace backend, Prometheus, Grafana), Grafana dashboard and example alert rules. **Done**; a test fails if the dashboard or alerts use a metric the adapter does not expose.
- [x] Documentation: configuration reference, sampling and retention guidance, privacy modes. **Done** ([observability.md](observability.md)).
- [x] Canary against the latest OpenTelemetry release in the compatibility matrix. **Done** (`otel` job in canary.yml).

**Exit criterion:** with `[otel]` installed and an exporter configured, one request produces a connected trace from the client through the adapter to Lemonade, the dashboards show the documented metrics, the no-content-leak and resilience tests pass, and the measured overhead is inside the budget.

## P3 — Lemonade-native architecture proposal

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

The conformance, interoperability, real-runtime and hardening gates are met; the remaining prerequisites are the P2 carry-over (hardware evidence) and the P2 specification, which gives the native design a feature registry, a contract-test suite and telemetry instruments to match.

- [ ] Map A2A endpoints onto the current Lemonade C++ HTTP layer.
- [ ] Identify the smallest reusable A2A core independent of Python SDK internals.
- [ ] Define Agent Card generation from Lemonade model/server capabilities.
- [ ] Define Task/Artifact state ownership and lifecycle inside `lemond`.
- [ ] Define streaming bridge from Lemonade generation events to A2A events.
- [ ] Define cancellation from A2A task → Lemonade request/backend execution (the Python reference now proves the behavior to match).
- [ ] Decide whether A2A shares Lemonade's primary port or uses a configurable listener (the sidecar already had to avoid Lemonade's WebSocket port 9000).
- [ ] Align CLI/configuration naming with Lemonade maintainers rather than assuming `--a2a`.
- [ ] Reuse the P2 specification: the native implementation claims features by registry id and must pass the same contract tests and emit the same telemetry instruments.
- [ ] Write an upstream design proposal before a large native implementation.

## P3 — Native prototype

Only start this after the proposal is accepted in principle.

- [ ] Prototype a minimal native C++ Agent Card endpoint.
- [ ] Prototype native SendMessage → Lemonade Router execution.
- [ ] Add native streaming.
- [ ] Add native cancellation/task lifecycle.
- [ ] Run the same black-box, contract and TCK suites against Python and native implementations.
- [ ] Compare correctness, TTFT, throughput, RSS and CPU overhead against the recorded baselines.
- [ ] Prepare an upstreamable patch series if maintainers accept the design.

## P4 — Capability expansion

These features are useful only after the core protocol surface is stable:

- [ ] Multi-model Agent Cards and skills based on Lemonade capabilities.
- [ ] Rich A2A parts for supported multimodal Lemonade endpoints (gated by the URL policy in `safe_urls.py`).
- [ ] A2A ↔ existing Lemonade API interoperability examples.
- [ ] Capability-aware delegation across multiple local Lemonade nodes.
- [ ] Privacy/locality policy for local and LAN agents.
- [ ] Optional hardware/capability metadata extensions (optional, namespaced, never required; see [architecture.md](architecture.md)).
- [ ] Quality/latency/energy benchmarking where hardware metrics are available.

## Explicitly out of scope

- [x] Remove CLM/LAYA/JEV or other "System One" model requirements from the architecture.
- [x] Do not build a second model-routing/reasoning framework inside the A2A adapter.
- [x] Do not make AMD hardware a protocol requirement.
- [x] No telemetry sent to the project: diagnostics, `doctor` and the support bundle are local, and exporting OpenTelemetry data is the operator's choice, off by default.

If Lemonade gains advanced model routing, scheduling or reasoning features, the A2A surface should reuse them through Lemonade's router instead of duplicating them.

## Recommended execution order

1. ~~Real Lemonade E2E~~ — done (CUDA, Vulkan, CPU; 1.7B and 4B).
2. ~~TTFT, load and cancellation evidence~~ — done on one machine.
3. ~~Official A2A TCK~~ — done for the protocol surface; the two SHOULD deviations are a TCK test artifact, reported upstream (#248) and covered by a workaround run.
4. ~~Independent-client interoperability~~ — done for the CLI and the JS, Go, .NET and Java SDKs plus the Inspector validators.
5. ~~Remaining hardening~~ — done: backpressure, per-user isolation, rate limiting, mTLS, profiles, fuzzing.
6. **P2, in this order**, because each step makes the next safer:
   1. *Specification, feature registry and `doctor`*, since observability and plugins should register against it.
   2. *OpenTelemetry*, with the privacy and resilience tests first.
   3. *Canary matrix, contract tests and extension API.*
   4. *Carry-over evidence*: reasoning-model handling, the 12B re-run, a second machine; the hardware items whenever such a machine is available.
7. **P3: upstream design proposal**, then a **native prototype** of the smallest accepted vertical slice, reusing the specification, contract tests and telemetry instruments.
8. **P4: capability expansion.**

This ordering keeps the repository evidence-driven and maximizes the chance that the work can be adopted upstream.
