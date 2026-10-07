# Changelog

## v0.1.0 - Reference Adapter (draft, not yet tagged)

First release of the Python reference adapter: an A2A 1.0 protocol surface in front of a Lemonade server.

### What is in it

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
