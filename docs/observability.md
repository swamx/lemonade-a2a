# Observability with OpenTelemetry

**Status: draft v0.1 (design only).** Nothing here is implemented except what is marked *exists today*. It defines the roadmap's *P2 observability* workstream.

## 1. Principles

1. **Configurable, with standard knobs first.** OpenTelemetry's own environment variables (`OTEL_*`) work as documented; project-specific settings exist only for what OpenTelemetry does not define.
2. **Off by default and free when off.** No exporter, no network, and the hot path pays only a no-op check. The core package depends on `opentelemetry-api` only (already installed transitively today, through `google-api-core` and `fastapi`; to be declared explicitly); the SDK and exporters come with an extra: `pip install lemonade-a2a[otel]`.
3. **Private by default.** This is a *local AI* system. Prompts, responses, task text, API keys and caller identities never leave the process unless the operator explicitly opts in, and then only to the extent chosen (see §5).
4. **Telemetry cannot hurt the service.** An unreachable collector, a slow exporter or a bug in instrumentation must never fail, slow or block a request (bounded queues, drop on overflow, timeouts).
5. **Low cardinality.** Metrics never carry task ids, context ids, user names or free text.
6. **Correlated.** One trace follows a request from the A2A client through the adapter to Lemonade, and logs carry the trace id.

*Exists today:* the A2A SDK already creates spans (`a2a-python-sdk`) whenever OpenTelemetry is installed and a provider is configured, and it can be switched off with `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`. The adapter adds nothing of its own yet, so any trace today shows SDK internals without the Lemonade call, task outcome or backpressure.

## 2. Signals

### Traces

```text
SERVER  POST /message:stream                      http.server.* attributes, a2a.binding, a2a.method
 ├─ (SDK spans: request handler, task manager)    already emitted by a2a-sdk
 └─ INTERNAL a2a.task.execute                     a2a.task.id, a2a.context.id, final a2a.task.state
     ├─ event  first_chunk                        TTFT marker
     ├─ event  cancel | disconnect | deadline | backpressure_stall
     └─ CLIENT lemonade.chat.stream               gen_ai.* attributes, server.address, http.response.status_code
```

- A task outlives its HTTP request, so `a2a.task.execute` is parented to the request that created it and every later request about the task (`GetTask`, `SubscribeToTask`, `CancelTask`) links to it with a **span link** instead of nesting.
- W3C `traceparent`/`tracestate` are honoured on incoming requests and forwarded to Lemonade. `baggage` is ignored unless enabled.
- Errors are recorded with the sanitized message already shown to clients, never the raw exception text (that stays in logs).

### Metrics

Standard instruments are used where a standard exists; adapter-specific ones live under `lemonade_a2a.`.

| Instrument | Type, unit | Attributes (all low cardinality) |
|---|---|---|
| `http.server.request.duration` | histogram, s | method, route template, status class |
| `lemonade_a2a.task.duration` | histogram, s | terminal state, binding, streaming |
| `lemonade_a2a.task.active` | up-down counter | |
| `lemonade_a2a.task.completed` | counter | terminal state (`completed`, `failed`, `canceled`, `rejected`) |
| `lemonade_a2a.task.rejected` | counter | reason (`concurrency`, `rate_limit`, `auth`, `size`, `profile`) |
| `lemonade_a2a.stream.ttft` | histogram, s | streaming |
| `lemonade_a2a.stream.chunk_interval` | histogram, s | |
| `lemonade_a2a.backend.errors` | counter | class (`timeout`, `status`, `connect`, `unreadable`) |
| `lemonade_a2a.backend.up` | gauge | |
| `lemonade_a2a.store.tasks`, `.evictions` | gauge, counter | |
| `lemonade_a2a.disconnects` | counter | policy (`kept`, `canceled`) |
| `gen_ai.client.operation.duration`, `gen_ai.client.token.usage` | histogram | model name; tokens only if Lemonade reports usage |
| `gen_ai.server.time_to_first_token`, `.time_per_output_token` | histogram, s | model name |

The GenAI semantic conventions are still in development upstream: the specification pins the convention version it follows, opt-in is through `OTEL_SEMCONV_STABILITY_OPT_IN`, and a convention change is a documented, versioned change here.

### Logs

Structured (JSON) records with `trace_id` and `span_id` on every line inside a request, a stable event name per record (`task.failed`, `backend.timeout`, `auth.rejected`), and the OpenTelemetry log bridge as an option. Prompt and response text is never logged (already true today); failure logs keep exception summaries only.

## 3. Configuration

| Setting | Purpose |
|---|---|
| `OTEL_SDK_DISABLED` | Master switch. The adapter additionally needs `LEMONADE_A2A_OTEL=1` (or the `[otel]` extra *and* an exporter setting) so that installing the extra never starts exporting by accident |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | Identity. Default `lemonade-a2a`; adds `service.version`, `a2a.sdk.version`, `lemonade.version` when known |
| `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER` | `otlp`, `console` or `none`, per signal |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `_PROTOCOL`, `_HEADERS`, `_TIMEOUT`, `_COMPRESSION`, `_CERTIFICATE` | OTLP over gRPC or HTTP/protobuf, TLS and auth headers |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | Head sampling (parent-based ratio by default when on) |
| `OTEL_METRIC_EXPORT_INTERVAL` | Metric push interval |
| `LEMONADE_A2A_OTEL_CAPTURE` | `none` (default), `metadata`, `content`; see §5 |
| `LEMONADE_A2A_OTEL_REDACT` | Extra attribute keys or patterns to drop |
| `LEMONADE_A2A_OTEL_PROMETHEUS` | Serve a pull endpoint `/metrics` (authenticated like other routes, off by default) for setups without a collector |
| `LEMONADE_A2A_OTEL_BUFFER` | Bound on queued telemetry before dropping |

Everything is also settable in the config file from [specification.md](specification.md), validated by `lemonade-a2a config validate`, and reported by `doctor` (exporter configured, endpoint reachable, last export error).

Interaction with the security profiles: under the `external` profile an OTLP endpoint must use TLS unless the operator explicitly accepts plaintext, and a Prometheus endpoint requires authentication.

## 4. Instrumentation approach

A thin internal module (`lemonade_a2a.telemetry`) wraps the API: `span()`, `counter()`, `histogram()` return no-ops when telemetry is off. The adapter code calls the module and never imports the OpenTelemetry SDK directly, so the dependency stays optional and plugins can replace the wiring (`lemonade_a2a.telemetry` entry point). Optional auto-instrumentation (`opentelemetry-instrumentation-asgi`, `-httpx`) is documented but not required, to avoid duplicate spans and extra overhead.

## 5. Privacy modes

| `LEMONADE_A2A_OTEL_CAPTURE` | Recorded | Use |
|---|---|---|
| `none` (default) | Timings, counts, states, sizes (character and chunk counts), model name, error classes | Production |
| `metadata` | Adds task and context ids on spans (not metrics), hashed caller identity, request parameters such as `max_tokens`, prompt and response lengths | Debugging a deployment |
| `content` | Adds prompt and response text as span events, truncated to a configured length | Local development only; the `external` profile refuses it |

A test generates random prompts and asserts that, in `none` and `metadata` modes, no prompt or response substring appears in any exported span, metric or log line.

## 6. Performance budget

Telemetry must not erode the measured baseline in [benchmarks.md](benchmarks.md). The benchmark harness gets three modes (off, on at 100% sampling, on at 10%) and `check_budget.py` enforces an additional limit on telemetry overhead, proposed as at most 1 ms median TTFT and 2% throughput at 100% sampling on the mock backend, to be confirmed by measurement before it becomes a gate.

## 7. Testing

- In-memory exporters assert the span tree, attribute names, events, links and metric values for success, failure, cancel, disconnect, deadline, rejection and backpressure paths, on both bindings.
- Cardinality test: thousands of tasks produce a bounded number of metric series.
- Resilience test: collector down, collector slow, exporter raising; requests keep their latency and succeed; shutdown flushes within a bound and does not hang.
- Matrix over the documented `OTEL_*` settings, including `OTEL_SDK_DISABLED`.
- Propagation test: an incoming `traceparent` reaches the mock Lemonade unchanged in trace id.
- A canary in the compatibility matrix runs these against the latest OpenTelemetry release.

## 8. Operator material

`examples/observability/`: a Docker Compose stack (OpenTelemetry Collector, a trace backend, Prometheus and Grafana), a Grafana dashboard (request rate and latency, task outcomes, TTFT, rejections by reason, backend health) and example alert rules (backend down, failure ratio, p95 TTFT regression). A short guide covers sampling, retention and the privacy modes.

## 9. Open questions

1. Does Lemonade expose its own telemetry or a request id the adapter can attach, so adapter and server traces can be joined?
2. Does the streaming response include token usage? If not, token metrics stay absent rather than estimated.
3. Which GenAI convention version to pin first, given they are still changing.
4. Whether a native Lemonade implementation (P3) should emit the same instruments, so dashboards survive the move. The instrument names above are chosen to be implementation-independent for that reason.
