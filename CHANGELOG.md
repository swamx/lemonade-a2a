# Changelog

## v0.2.0 - Agents that talk to agents (draft, not yet tagged)

Agents served by the adapter can now find other A2A agents through an agent marketplace and delegate work to them. All of it is **experimental** in the feature registry. Start with the new [multi-agent pathway](docs/pathways/multi-agent.md); the reference is [docs/marketplace.md](docs/marketplace.md).

### What is new

- **Agent marketplaces**: give an agent a marketplace's link or bare domain name (`LEMONADE_A2A_MARKETPLACE`, `serve --marketplace`). Well-known listing paths and the common listing shapes (arrays, `{"agents": [...]}`, embedded cards, card URLs, base URLs) are read, and every card is validated. Every listing, card and call goes through the SSRF-guarded transport, with no redirects and with bounded sizes and counts.
- **DNS SRV discovery**: `_a2a-registry._tcp.<domain>` (a listing) and `_a2a._tcp.<domain>` (one agent), RFC 2782 ordering, an optional TXT `path=`; `dns:<domain>` uses DNS only. DNS targets pass the same URL policy, and the marketplace API key is never sent to them.
- **Delegation, several agents per task**: the local model plans which agents to ask. Agents in one round run in parallel; while budget is left (`LEMONADE_A2A_MAX_DELEGATIONS`, default 3) another round can build on the replies. `@agent-a @agent-b` asks agents directly. Replies are composed by the local model or relayed (`LEMONADE_A2A_MARKETPLACE_REPLY`). A remote failure never fails the task.
- **Multi-turn conversations**: the last turns of a `contextId` reach the model (`LEMONADE_A2A_CONVERSATION_TURNS`, default 10), per authenticated caller and in memory only. Each remote agent keeps its own remote conversation. A remote `input-required` pauses the local task with the remote question, and the caller's reply resumes the remote task.
- **File and data parts** (opt-in, `LEMONADE_A2A_FILES=1`): text files and JSON go to the model as fenced data, images go as `image_url` parts with `LEMONADE_A2A_FILE_IMAGES=1`, and URL parts are passed as references (never fetched). The Agent Card lists exactly the accepted media types. Parts are forwarded to agents whose card accepts them, and returned files become a `delegated-files` artifact.
- **Client API**: `A2AClient.ask()` (polls running tasks, typed errors, `context_id`/`task_id` continuation, `parts`), an optional pre-validated `card=`, JSON-RPC and HTTP+JSON negotiation; `Marketplace` (also a `DiscoveryProvider`) with `agents()`, `get()`, `ask()`.
- **CLI**: `lemonade-a2a agents list|ask`; `serve --name --description --model --public-url --lemonade-url --marketplace --marketplace-allow-local --marketplace-reply`. Several agents can run on one machine with different `--port` values.
- **Docs**: [pathways](docs/pathways/README.md) (guided, step-by-step routes) starting with multi-agent systems; marketplace reference; security, architecture, protocol-mapping, observability and upgrading updated.

### Fixed

- A delegated call used the marketplace's 20 s listing timeout as its HTTP timeout, so a remote agent that took longer (for example queued behind other calls on a single-model Lemonade) was reported as unreachable well before `LEMONADE_A2A_DELEGATION_TIMEOUT_SECONDS`. The HTTP timeout now covers the whole wait. Found on a real model.
- Outbound connections to a host with several addresses now try the next vetted address when one refuses the connection. `localhost` often resolves to `::1` first while agents listen on `127.0.0.1`, which made agents on one machine unreachable from each other.
- A reply to a task in `input-required` continues that task. The executor used to publish a fresh `SUBMITTED` task, which overwrote the stored history and artifacts.
- `serve --port N` without `--public-url` advertises `http://localhost:N` in the card (it advertised the default port).

### Evidence

- **Canary** (`scripts/canary.py`, one clean environment per SDK): the full suite, 567 tests with 0 failures, on a2a-sdk **1.2.0, 1.2.3 and 1.3.1** (the newest in the `<1.4` range); the compatibility manifest is generated from that run. 1.2.1 and 1.2.2 were tested for 0.1.0 and are in range but not re-run, so `doctor` reports them as untested.
- Coverage 96.9%. The new end-to-end tests run real agents over sockets: delegation through a marketplace domain, an `input-required` round trip between two agents, files in both directions, and the localhost address fallback.
- The [multi-agent pathway](docs/pathways/multi-agent.md) was run command by command against the mock Lemonade server.
- The CI jobs on the pull request passed: Python 3.11-3.13, a2a-sdk 1.2.0 / latest / unbounded, the official TCK, CodeQL, bandit, pip-audit, secret scanning and dependency review.
- **Real Lemonade 2026.40.0** (release checklist step 3, 2026-11-25): `doctor --deep` PASS; `validate_real_lemonade.py` all passed (integration suite, end to end, cancellation proof, benchmark); an interleaved 30-run A/B against 0.1 shows no overhead regression; the multi-agent pathway on a real model passed 8/8 checks with Gemma-3-4B (7/8 with the 1.7B Bonsai, a routing mistake by the small model). Details: [docs/real-lemonade-validation.md](docs/real-lemonade-validation.md).
- **Not done**: use against a public marketplace or real DNS records (unit tests use fakes); hardware beyond this one machine.

### Known limitations

- Planning quality depends on the local model. A 1.7B model can send a request to the wrong agent, and small models sometimes copy the `<agent_reply>` fence tags into the answer. Prefer a 4B-class model or larger for the planner, or use `@mentions` / `relay`.
- On one machine with one model, Lemonade runs one generation at a time, so "parallel" delegations queue. Keep `LEMONADE_A2A_DELEGATION_TIMEOUT_SECONDS` generous.
- The TTFT overhead budget (25 ms) is close to the measured overhead on the validation machine for both 0.1 and 0.2 (14-33 ms per 30-run round).

### Upgrade notes

- **Conversation memory is on by default.** Messages that reuse a `contextId` now include earlier turns. Set `LEMONADE_A2A_CONVERSATION_TURNS=0` for the 0.1 behaviour.
- **Continuing an `input-required` task keeps its history** (see Fixed). Clients that relied on the old reset behaviour should start a new task instead.
- **New core dependency**: `dnspython` (pure Python).
- `fetch_agent_card` rejects a card that advertises no endpoint; `A2AClient` also negotiates HTTP+JSON.
- Nothing changes unless you set `LEMONADE_A2A_MARKETPLACE` or `LEMONADE_A2A_FILES`, apart from conversation memory and the fixes above. Details: [docs/upgrading.md](docs/upgrading.md#from-01-to-02).

## v0.1.0 - Reference Adapter (draft, not yet tagged)

First release of the Python reference adapter: an A2A 1.0 protocol surface in front of a Lemonade server.

### What is in it

- **Benchmarks separate transport from output**: `chunks_per_s` is now labelled a transport metric, and the report adds `output_tokens` and `tokens_per_s` (assembled answer, one tokenizer for both paths, `--tokenizer`), plus `ttft_reasoning_ms` so first reasoning output and first user-visible answer are recorded separately. `check_budget.py` and `telemetry_overhead.py` gate throughput on tokens/s. Regression tests send the same text as one event and as many events. Suggested by an outside reviewer.

- **A2A 1.0** Agent Card, JSON-RPC and HTTP+JSON (served at the base URL; legacy `/a2a/jsonrpc` and `/a2a/rest` kept), SSE streaming, task lifecycle, cancellation and error mapping.
- **Cancellation reaches the backend**: `CancelTask` stops generation on the real Lemonade server.
- **Failure handling**: backend errors, timeouts and bad input become well-formed A2A errors or `FAILED`/`REJECTED` tasks with sanitized messages.
- **Resource bounds and security**: input size/part limits, request-body limit (413), per-task deadline, concurrency cap, bounded task store, optional API-key auth, TLS, no public OpenAPI/docs pages, security headers, graceful shutdown.
- **Exposure profiles** (`local` / `lan` / `external`) that refuse unsafe combinations at startup, **named API keys with per-user task isolation**, **rate limiting**, **mutual TLS**, and an opt-in **cancel-on-disconnect** mode.
- **Lifecycle hardening**: a stream that fails or times out part-way closes its artifact before `FAILED`; slow-consumer backpressure verified end to end; cancel/disconnect races tested.
- **Fuzzing** (property-based) of requests, parts, metadata and backend stream lines, which found and fixed: non-UTF-8 bodies returning an internal error, and wrongly shaped backend stream events crashing the stream parser.
- **Compatibility specification you can run**: `lemonade-a2a doctor` cross-validates the installed `a2a-sdk`, `lemonade-a2a` and Lemonade against a generated manifest and a feature registry (49 features, each with evidence checked in CI); `capabilities`, `config show|validate|schema`, `support-bundle`, `doctor --sdk-gap`; `LEMONADE_A2A_COMPAT=off|warn|strict`; feature flags (`LEMONADE_A2A_FEATURES`); layered configuration (defaults, TOML file, environment, flags); an authenticated, opt-in capabilities endpoint; a weekly **canary** against the lowest, latest and pre-release `a2a-sdk`; **backend contract tests** with recorded Lemonade streams and OpenAI, llama.cpp and vLLM shapes; a package build-and-install check. See `docs/specification.md`, `docs/features.md`, `docs/upgrading.md`.
- **OpenTelemetry**: opt-in traces, metrics and logs (`LEMONADE_A2A_OTEL=1`, `lemonade-a2a[otel]`), standard `OTEL_*` configuration, W3C trace propagation to Lemonade, span links for later requests about a task, low-cardinality metrics, optional authenticated Prometheus endpoint, JSON logs with trace ids, privacy modes (prompts are never exported by default; property-tested), bounded buffers so a dead or slow collector cannot affect requests, a measured overhead budget, and an example collector/Grafana stack with alerts. See `docs/observability.md`.
- **Extension API v1** (backends, task stores, authenticators, telemetry) and a **persistent SQLite task store** (`LEMONADE_A2A_TASK_STORE=sqlite`, `lemonade-a2a[sqlite]`) that survives restarts, reports orphaned running tasks as failed and bounds its size. See `docs/extending.md`.
- **Reasoning models and backend errors**: `reasoning_content` is dropped by default or streamed as a separate artifact (`LEMONADE_A2A_REASONING`); a response that is all reasoning fails with a clear message. An error Lemonade reports *inside* an HTTP 200 stream (for example a prompt longer than the model's context window) now fails the task with a safe message; before, it completed with an empty answer. Found by the Gemma-4-12B benchmark cells.
- **TCK workaround** for [a2a-tck#248](https://github.com/a2aproject/a2a-tck/issues/248): the official run must show only the two known deviations; a second run with a two-line patch is clean (161 passed, 0 deviations); both are enforced in CI.
- **Automated gates**: CodeQL, bandit, pip-audit, secret scanning, dependency review and a 95% coverage threshold (99% measured) on every pull request.
- **Evidence** (see `docs/`):
  - official A2A TCK against the protocol surface: 157 passed, 0 failed, 4 expected-fail (SHOULD, a TCK test artifact), the rest skipped for undeclared capabilities; `docs/conformance-results.json`;
  - independent clients (A2A CLI, JS SDK, Go library, .NET, Java, Inspector validators) over both bindings, mock and real Lemonade, each 10/10; `docs/interoperability.md`;
  - opt-in real-Lemonade `pytest` suite with automatic failure diagnostics and a one-command validation script;
  - **real inference on a second machine**: a CI workflow runs an actual Lemonade 2026.40.0 with a small model on a Linux runner (CPU) and passes `doctor --deep`, the integration suite, the end-to-end check and the cancellation proof;
  - pinned-TCK and benchmark-budget CI workflows;
  - real Lemonade 2026.40.0 on llama.cpp CUDA, Vulkan and CPU with a 1.7B and a 4B model, plus long-prompt and long-output workloads and a defined overhead budget; Gemma-4-12B only partly measured and over budget on CUDA (+1.6 s, likely noise, unconfirmed); `docs/benchmarks.md`;
  - deterministic mock-Lemonade black-box E2E in CI (Python 3.11-3.13).

### Measured

- A2A adds roughly 5-15 ms median time-to-first-token over calling Lemonade directly; throughput under concurrent load (N up to 8) is bounded by the backend, with no adapter-added cost.
- Adapter footprint about 70 MB RSS and near-zero idle CPU.

### Known limitations

- The TCK run uses a scenario executor behind the real server layer; it certifies the protocol surface, not Lemonade inference.
- Not covered: ITK (not applicable to a standalone adapter), NPU/ROCm/Metal and non-llama.cpp backends (no hardware), gRPC, push notifications, extended Agent Card, OAuth/OIDC inside the adapter (use a gateway), URL/file fetching (rejected; policy in `safe_urls.py` is ready but unused).
- Not implemented: merging third-party plugins' own registry entries into `capabilities`; a release gate (there is no release pipeline yet, and publishing to PyPI needs the owner's approval); token-usage metrics when the backend reports no usage (Lemonade 2026.40.0 streams do not).
- Slow-consumer backpressure relies on the A2A SDK's bounded queue (1,024 events), which is not configurable here.
- The A2A CLI v0.3.0 cannot discover an agent whose card declares auth (a2a-go #430, fixed in a2a-go v2.6.0; a CLI release bundling it is needed); connect with `--endpoint`.
- Java SDK 1.4.0 rejects `ListTasks` responses whose `pageSize` differs from the task count (stricter than the A2A spec example).
- Pre-alpha: pinning and API details may change.

### Upgrade notes

- The default port is 9100 (it was 9000, which Lemonade Server uses for its WebSocket).
- **Binding beyond loopback now needs a profile.** `LEMONADE_A2A_HOST=0.0.0.0` with the default `local` profile refuses to start; set `LEMONADE_A2A_PROFILE=lan` (with an API key) or `external` (key, TLS and a rate limit). This replaces the old startup warning.

- **New core dependencies**: `opentelemetry-api` and `packaging` (the API is already present transitively; both are small). The OpenTelemetry SDK and exporters (`[otel]`) and SQLAlchemy/aiosqlite (`[sqlite]`) are optional extras.
- **The `lemonade-a2a` command is now a CLI.** With no arguments, or only flags, it still means `serve`, so existing service definitions keep working; `python -m lemonade_a2a` works too. `lemonade_a2a.server:main` still exists.
- **Behaviour changes**: an error event inside a 200 stream, and a response that is all reasoning, now fail the task instead of completing it empty. `LEMONADE_A2A_COMPAT` defaults to `warn` (it only logs).
- **The A2A SDK's own spans are off by default** under `lemonade-a2a serve` (`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false` unless you set it): its tracing decorators cost 2-5 ms per request warm and up to about 16 ms in bursts even with no telemetry configured (measured; `docs/benchmarks.md`). The adapter's own spans cover request, task and Lemonade call; set the variable to `true` to get the SDK's spans (they carry JSON-RPC method names) back.
- **Privacy of the SQLite store**: the task database contains prompts and answers; it is created owner-only (0600) on POSIX and should be protected like any data store.

See `docs/roadmap.md` for what comes next (P2 remainder: hardware evidence and real inference on a second machine; P3: the Lemonade-native proposal and prototype; P4: capability expansion).
