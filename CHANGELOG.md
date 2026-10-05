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
- **Automated gates**: CodeQL, bandit, pip-audit, secret scanning, dependency review and a 95% coverage threshold (99% measured) on every pull request.
- **Evidence** (see `docs/`):
  - official A2A TCK against the protocol surface: 157 passed, 0 failed, 4 expected-fail (SHOULD, a TCK test artifact), the rest skipped for undeclared capabilities; `docs/conformance-results.json`;
  - independent clients (A2A CLI, JS SDK, Go library, .NET, Java, Inspector validators) over both bindings, mock and real Lemonade, each 10/10; `docs/interoperability.md`;
  - opt-in real-Lemonade `pytest` suite with automatic failure diagnostics and a one-command validation script;
  - pinned-TCK and benchmark-budget CI workflows;
  - real Lemonade 2026.40.0 on llama.cpp CUDA, Vulkan and CPU with a 1.7B and a 4B model, plus long-prompt and long-output workloads and a defined overhead budget; Gemma-4-12B only partly measured and over budget on CUDA (+1.6 s, likely noise, unconfirmed); `docs/benchmarks.md`;
  - deterministic mock-Lemonade black-box E2E in CI (Python 3.11-3.13).

### Measured

- A2A adds roughly 5-15 ms median time-to-first-token over calling Lemonade directly; throughput under concurrent load (N up to 8) is bounded by the backend, with no adapter-added cost.
- Adapter footprint about 70 MB RSS and near-zero idle CPU.

### Known limitations

- The TCK run uses a scenario executor behind the real server layer; it certifies the protocol surface, not Lemonade inference.
- Not covered: ITK (not applicable to a standalone adapter), NPU/ROCm/Metal and non-llama.cpp backends (no hardware), gRPC, push notifications, extended Agent Card, OAuth/OIDC inside the adapter (use a gateway), persistent task storage, URL/file fetching (rejected; policy in `safe_urls.py` is ready but unused).
- Slow-consumer backpressure relies on the A2A SDK's bounded queue (1,024 events), which is not configurable here.
- The A2A CLI v0.3.0 cannot discover an agent whose card declares auth (a2a-go #430, fixed in a2a-go v2.6.0; a CLI release bundling it is needed); connect with `--endpoint`.
- Java SDK 1.4.0 rejects `ListTasks` responses whose `pageSize` differs from the task count (stricter than the A2A spec example).
- Pre-alpha: pinning and API details may change.

### Upgrade notes

- The default port is 9100 (it was 9000, which Lemonade Server uses for its WebSocket).
- **Binding beyond loopback now needs a profile.** `LEMONADE_A2A_HOST=0.0.0.0` with the default `local` profile refuses to start; set `LEMONADE_A2A_PROFILE=lan` (with an API key) or `external` (key, TLS and a rate limit). This replaces the old startup warning.

See `docs/roadmap.md` for what comes next (v0.2: lifecycle hardening and more backends; v0.3: upstream design and a minimal native slice).
