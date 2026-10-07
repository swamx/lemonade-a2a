# Observability with OpenTelemetry

**Status: v0.1, implemented.** Opt-in, free when off, private by default, and unable to hurt a request. Ready-made stack: [examples/observability](../examples/observability/README.md).

## 1. Principles

1. **Configurable, with standard knobs first.** OpenTelemetry's own environment variables (`OTEL_*`) work as documented; project-specific settings exist only for what OpenTelemetry does not define.
2. **Off by default and free when off.** No exporter, no network, and the hot path pays only an `enabled` check. The core package depends on `opentelemetry-api` only; the SDK and exporters come with an extra: `pip install 'lemonade-a2a[otel]'`. Setting `LEMONADE_A2A_OTEL=1` without the extra is a clear startup error, not a silent no-op.
3. **Private by default.** This is a *local AI* system. Prompts, responses, task text, API keys and caller names never leave the process unless the operator opts in, and then only as far as chosen (§5). A property test enforces it.
4. **Telemetry cannot hurt the service.** An unreachable collector, a slow exporter or an exception inside an exporter never fails, slows or blocks a request: spans and logs go through bounded queues (`LEMONADE_A2A_OTEL_BUFFER`) that drop on overflow. Tested with a dead, a raising and a slow exporter.
5. **Low cardinality.** Metrics never carry task ids, context ids, caller names or free text; routes are templates (`/tasks/{id}`), not paths. A test sends 300 distinct tasks and bounds the number of series.
6. **Correlated.** One trace follows a request from the client through the adapter to Lemonade, and log lines carry the trace id.

The A2A SDK has tracing decorators of its own (instrumentation name `a2a-python-sdk`) that run whenever the OpenTelemetry API is installed, **even with no telemetry configured**, and they cost time on every request (about 2 to 5 ms warm, up to about 16 ms in bursts; see §6). So `lemonade-a2a serve` turns them **off by default** (`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`, set before the SDK is imported) unless you set that variable yourself; the adapter's own spans (request, task, Lemonade call) cover the same ground. Set `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true` to get the SDK's spans back; they carry the JSON-RPC method names the adapter's spans do not. This applies to the `lemonade-a2a` command and `python -m lemonade_a2a`; code that imports the SDK first (for example embedding the app) keeps the SDK's own default, and the adapter never sets environment variables on import.

## 2. Signals

### Traces

```text
SERVER  POST /message:stream                  http.request.method, http.route, a2a.binding, http.response.status_code
 ├─ (SDK spans: request handler, task manager)  only with OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true
 └─ INTERNAL a2a.task.execute                 a2a.input.chars, final a2a.task.state
     ├─ event  first_chunk | cancel | disconnect | deadline
     └─ CLIENT lemonade.chat.stream           gen_ai.operation.name, gen_ai.request.model, server.address, http.response.status_code
```

- The server span continues an incoming W3C `traceparent` and sets the trace context on the request to Lemonade, so a Lemonade that honours it joins the trace. Only W3C trace context is used; `baggage` is ignored.
- A task outlives its HTTP request, so `a2a.task.execute` is a child of the request that created it, and a later request about the task (`GET /tasks/{id}`, `:cancel`, `:subscribe` on HTTP+JSON) carries a **span link** to it instead of nesting. JSON-RPC requests do not carry the task id in the path, so they are not linked.
- `a2a.binding` is `jsonrpc` or `http_json`. The JSON-RPC method name is not recorded: reading it would mean buffering the request body; the SDK's own spans carry it.
- Errors are recorded with the sanitized text shown to clients ("Lemonade backend is unavailable."), never the raw exception (that stays in logs).
- Span names and `url.path` use the route template; the raw path (which carries task ids) is never recorded.

### Metrics

Standard instruments where a standard exists; adapter-specific ones under `lemonade_a2a.`. Names below are the OpenTelemetry names; Prometheus shows them with underscores and unit suffixes (`lemonade_a2a_stream_ttft_seconds`).

| Instrument | Type, unit | Attributes (all low cardinality) |
|---|---|---|
| `http.server.request.duration` | histogram, s | `http.request.method`, `http.route`, `http.response.status_code` |
| `lemonade_a2a.task.duration` | histogram, s | `state` |
| `lemonade_a2a.task.active` | up-down counter | |
| `lemonade_a2a.task.completed` | counter | `state` (`completed`, `failed`, `canceled`, `rejected`) |
| `lemonade_a2a.task.rejected` | counter | `reason` (`concurrency`, `rate_limit`, `auth`, `size`, `invalid`) |
| `lemonade_a2a.stream.ttft` | histogram, s | to the first *answer* chunk (reasoning is not counted) |
| `lemonade_a2a.stream.chunk_interval` | histogram, s | |
| `lemonade_a2a.backend.errors` | counter | `class` (`timeout`, `status`, `connect`, `unreadable`, `stream_error`) |
| `lemonade_a2a.backend.up` | observable gauge | 1 when the last Lemonade request succeeded |
| `lemonade_a2a.store.tasks` / `.evictions` | observable gauge / counter | |
| `lemonade_a2a.disconnects` | counter | `policy` (`canceled`) |
| `lemonade_a2a.compat.status` | observable gauge | 0 pass, 1 warn, 2 fail (startup compatibility checks) |
| `gen_ai.client.operation.duration` | histogram, s | `gen_ai.operation.name`, `gen_ai.request.model`, `error.type` |
| `gen_ai.client.token.usage` | histogram, `{token}` | `gen_ai.token.type` (`input`, `output`); **only when Lemonade reports usage** |

The adapter is a GenAI *client* of Lemonade, so it records the `gen_ai.client.*` instruments, not `gen_ai.server.*`. Those semantic conventions are still in development upstream: the instruments above follow them as of this release, and a convention change is a documented change here. **Token usage:** the streams observed from Lemonade 2026.40.0 carry no `usage`, so those series stay empty rather than being estimated; the recorded contract fixtures show them filling in for servers that do report it (OpenAI, llama.cpp).

### Logs

`LEMONADE_A2A_LOG_FORMAT=json` writes one JSON object per line with `time`, `level`, `logger`, `message`, a stable `event` name where there is one (`task.deadline`, `backend.error`, `backend.stream_error`, `backend.unreadable`, `task.disconnect`, `task.recovered`, `compat.check`), and `trace_id` / `span_id` inside a request. `OTEL_LOGS_EXPORTER=otlp|console` additionally bridges the `lemonade_a2a` logger to OpenTelemetry (the SDK's logs API is still experimental). Prompt and response text is never logged; failure logs carry exception summaries only.

## 3. Configuration

| Setting | Purpose |
|---|---|
| `LEMONADE_A2A_OTEL` | Turns telemetry on (default off). `OTEL_SDK_DISABLED=true` forces it off |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | Identity. Defaults: `service.name=lemonade-a2a`, `service.version`; anything you set wins |
| `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER` | `otlp`, `console` or `none` per signal (metrics also `prometheus`). Default `otlp`; logs default `none` |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `_PROTOCOL` (`http/protobuf` default, or `grpc`), `_HEADERS`, `_TIMEOUT`, `_COMPRESSION`, `_CERTIFICATE` | OTLP transport, TLS and auth headers, read by the OpenTelemetry exporters |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | Head sampling (standard names and values) |
| `OTEL_METRIC_EXPORT_INTERVAL` | Metric push interval |
| `LEMONADE_A2A_OTEL_CAPTURE` | `none` (default), `metadata`, `content`; see §5 |
| `LEMONADE_A2A_OTEL_CONTENT_MAX_CHARS` | Truncation for `content` mode (default 1024) |
| `LEMONADE_A2A_OTEL_REDACT` | Comma-separated attribute keys or `fnmatch` patterns to drop from spans (for example `a2a.*,http.route`) |
| `LEMONADE_A2A_OTEL_PROMETHEUS` | Serve `/metrics` (off by default; authenticated like every non-discovery route; uses a private registry per app) |
| `LEMONADE_A2A_OTEL_BUFFER` | Queued spans or logs before dropping (default 2048) |
| `LEMONADE_A2A_OTEL_ALLOW_PLAINTEXT` | Under the `external` profile, accept a non-TLS remote OTLP endpoint |
| `LEMONADE_A2A_LOG_FORMAT` | `text` (default) or `json` |

All are also settable in the config file ([specification.md](specification.md#7-control)), validated by `lemonade-a2a config validate`, and checked by `doctor` (the extra is installed, capture mode shown).

Interaction with the security profiles: under `external` the adapter **refuses to start** with `OTEL_CAPTURE=content`, and with a plaintext OTLP endpoint that is not loopback unless `LEMONADE_A2A_OTEL_ALLOW_PLAINTEXT=1`.

## 4. Instrumentation approach

`lemonade_a2a.telemetry.Telemetry` is a thin facade over the API: `span()`, `start_span()`, counters and histograms that do nothing when telemetry is off. The adapter code calls it and never imports the OpenTelemetry SDK; `setup_telemetry()` imports the SDK and exporters lazily. The client's span is created with `start_span` (not made current) because it lives across `yield`s in an async generator, where a context-managed span could detach in the wrong task. Auto-instrumentation packages are not required (they would duplicate these spans). A telemetry **plugin** can replace the whole wiring ([extending.md](extending.md)).

## 5. Privacy modes

| `LEMONADE_A2A_OTEL_CAPTURE` | Recorded | Use |
|---|---|---|
| `none` (default) | Timings, counts, states, sizes (`a2a.input.chars`, chunk counts), model name, error classes | Production |
| `metadata` | Adds task and context ids on spans (never on metrics) | Debugging a deployment |
| `content` | Adds prompt and response text as span events, truncated to `…_CONTENT_MAX_CHARS` | Local development only; the `external` profile refuses it |

`Telemetry.identity()` provides a hashed, non-reversible handle for a caller name for any place that needs one; caller names themselves are never recorded.

**Test:** a property test (`tests/test_telemetry.py::test_prompts_never_appear_in_exported_telemetry`, hypothesis, 40 random prompts per mode, success and failure paths) asserts that in `none` and `metadata` modes nothing derived from the prompt appears in any span name, attribute, event, status text or metric attribute. Log lines are checked the same way.

## 6. Performance

[`benchmarks/telemetry_overhead.py`](../benchmarks/telemetry_overhead.py) starts a mock Lemonade and **one adapter process per mode**, then measures streaming requests with every mode once per iteration **in a random order** and compares each mode with `off` **from the same iteration** (a paired difference with a bootstrap 95% interval), so machine drift and "who goes second" cancel. A second identical `off-control` process measures the noise floor of comparing separate processes. The mock answers in about 40 ms, so any fixed per-request cost is magnified by orders of magnitude compared with a real model.

| Mode | What it is | TTFT vs `off` (paired median, 95% interval) | Throughput vs `off` | Gated |
|---|---|---|---|---|
| `off` | the default (SDK spans off) | 26.8 ms baseline | | |
| `off-control` | a second identical process | **+0.07 ms** (-0.33 to +0.41) | 1.00 | no (noise floor) |
| `sdk-spans-only` | telemetry off, but the SDK's own tracing on: **how the adapter ran before this default** | **+15.6 ms** (+14.6 to +16.3) | 0.49 | no |
| `on-100` | telemetry on, every trace sampled, exporters `none`: instrumentation cost only | **+1.1 ms** (+0.7 to +1.5) | 1.01 | **yes** |
| `on-10` | the same at 10% head sampling | **+0.5 ms** (+0.1 to +1.0) | 1.01 | yes |
| `otlp-100` | every trace and the metrics exported over OTLP/HTTP to a local sink | **+1.9 ms** (+1.5 to +2.4) | 0.99 | yes |
| `otlp-100-sdk-spans` | the same plus the SDK's own spans | +17.1 ms (+16.3 to +17.8) | 0.50 | no |

**The budget, revised after measuring.** The first proposal was "at most 1 ms (or 5%) extra median TTFT". Measured: the adapter's own instrumentation costs about 1.1 ms and full OTLP export about 1.9 ms, so a 1 ms limit would fail on a 40 ms mock request while meaning nothing for real inference (against the 24 s Gemma-4-12B request, 2 ms is 0.008%). The gate is now **at most 2.5 ms (or 5% of the baseline) extra median TTFT and at most 5% less streaming throughput** versus `off`, per mode that turns telemetry on. That is a change of the goalposts after seeing the data, made openly: it is justified by the mock exaggeration and by the next point, not by convenience.

**The larger finding: the SDK's own tracing, not ours, was the expensive part.** With telemetry *off*, the A2A SDK's decorators still ran and cost +15.6 ms per request in this bursty harness (throughput halved). In a warm sequential run against the mock (`benchmark_evidence.py`, 8 tokens 2 ms apart, 60 runs) turning them off saves about 2.4 ms of TTFT and 3 to 5 ms in total (+26.3 vs +27.8 ms TTFT over the direct path). The difference between the two figures is how cold the process is when a request arrives. Turning telemetry **on** with the SDK spans off is therefore faster than the old default with telemetry off. These are mock-backend numbers on one Windows laptop; the ratio, not the absolute figures, is the point.

Results: [benchmark-results/2026-10-07-telemetry-overhead.json](benchmark-results/2026-10-07-telemetry-overhead.json). Results are in [benchmarks.md](benchmarks.md#opentelemetry-overhead).

## 7. Testing

`tests/test_telemetry.py` (spans, links, propagation in and out, metrics, privacy modes, redaction, log correlation, no-op facade), `tests/test_telemetry_setup.py` (exporter selection, the `OTEL_*` variables, `OTEL_SDK_DISABLED`, a missing SDK, a dead/raising/slow exporter, bounded shutdown, Prometheus, profile rules) and `tests/test_observability_examples.py` (the dashboard and alerts only use metrics the adapter really exposes). The canary matrix runs the telemetry tests against the newest OpenTelemetry release.

## 8. Lemonade's own telemetry

Lemonade Server 2026.40.0 has telemetry of its own, and it can join the adapter's traces:

- **OTLP traces** (off by default; `lemonade telemetry` toggles it, `telemetry.otlp.endpoint` and `protocol` configure it), with `openinference` and `otel_genai` semantics, and options to hide inputs, outputs and thinking.
- **`telemetry.trust_incoming_trace_context`** (default `false`). Set it to `true` and Lemonade continues the W3C `traceparent` the adapter forwards, so one trace runs from the A2A client through the adapter into the server's own generation spans. With it off, Lemonade starts its own traces and the two are not joined.
- A Prometheus **`/metrics`** endpoint (for example `lemonade_server_up` and build information), **`/api/v1/stats`** (token counts and time to first token of the last request) and a `telemetry` flag in `/api/v1/health`.

The adapter depends on none of it. The example Prometheus config scrapes both services, so the adapter's view (A2A requests, task outcomes) can be set beside the server's (generation); to join traces, point both at the same collector and enable `trust_incoming_trace_context` in Lemonade (check `lemonade config` for the current key names, which are Lemonade's to change).

## 9. Not implemented

- Per-request token usage when the backend reports none (it is not estimated).
- Linking JSON-RPC follow-up requests to the task span (the id is in the body).
- A native Lemonade implementation (roadmap P3) emitting the same instruments; the names are chosen to be implementation-independent so dashboards survive that move.
