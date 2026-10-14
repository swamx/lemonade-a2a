# Observability example

A local stack for [lemonade-a2a's OpenTelemetry support](../../docs/observability.md): OpenTelemetry
Collector, Jaeger for traces, Prometheus for metrics, and Grafana with a ready dashboard and alerts.

## Run it

```bash
# 1. the stack
cd examples/observability
docker compose up -d

# 2. the adapter, exporting to the collector (needs the otel extra)
pip install 'lemonade-a2a[otel]'
export LEMONADE_A2A_OTEL=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_SERVICE_NAME=lemonade-a2a          # the default
export LEMONADE_A2A_LOG_FORMAT=json            # log lines carry trace_id / span_id
lemonade-a2a

# 3. generate some traffic, then open http://localhost:3000 ("Lemonade A2A") and http://localhost:16686
```

On Windows PowerShell use `$env:NAME = "value"` instead of `export`.

Without a collector, the adapter can serve Prometheus itself (`LEMONADE_A2A_OTEL_PROMETHEUS=1`,
authenticated like every other route); see the commented scrape job in `prometheus.yml`.

## What you get

| Panel | Metric | Reading it |
|---|---|---|
| Backend up | `lemonade_a2a_backend_up` | 0 means the last Lemonade request failed |
| Compatibility status | `lemonade_a2a_compat_status` | 0 pass, 1 warn, 2 fail; run `lemonade-a2a doctor` |
| Request rate / latency | `http_server_request_duration_seconds` | by status code; includes refused requests |
| Task outcomes / duration | `lemonade_a2a_task_completed_total`, `_task_duration_seconds` | `completed`, `failed`, `canceled`, `rejected` |
| Time to first token | `lemonade_a2a_stream_ttft_seconds` | to the first *answer* chunk (reasoning is not counted) |
| Rejections | `lemonade_a2a_task_rejected_total` | `concurrency`, `rate_limit`, `auth`, `size`, `invalid` |
| Backend errors | `lemonade_a2a_backend_errors_total` | `timeout`, `status`, `connect`, `unreadable`, `stream_error` |

A trace for one request shows `POST /message:send` (server), `a2a.task.execute` (with `first_chunk`,
`cancel`, `disconnect`, `deadline` events) and `lemonade.chat.stream` (client, with the W3C trace
context forwarded to Lemonade). Later requests about a task (`GET /tasks/{id}`) carry a span *link* to
the execution, because a task outlives the request that started it.

## Privacy

By default **no prompt or response text is ever exported**, and task and context ids stay off spans.
`LEMONADE_A2A_OTEL_CAPTURE=metadata` adds ids and a hashed caller handle; `content` adds truncated
prompt and response text and is for local development only (the `external` profile refuses it). The
collector config here deletes any `content` attribute as a second line of defence.

## Checking the files stay true

`tests/test_observability_examples.py` fails if the dashboard or the alert rules name a metric that
the adapter does not expose, so they cannot drift from the code.
