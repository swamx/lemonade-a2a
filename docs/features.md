# Feature registry

Generated from [`features.json`](../src/lemonade_a2a/spec/features.json) by `scripts/generate_features_doc.py`; do not edit by hand. The same data is available at runtime from `lemonade-a2a capabilities` and, when enabled, from the capabilities endpoint. Specification version 0.1; see [specification.md](specification.md).

**State**: `supported` works and has evidence; `unsupported` is deliberately not offered (and is rejected, not ignored); `observed` is behaviour of a dependency the adapter relies on or works around; `unknown` is not yet established. **Default** shows whether the feature is in effect with no configuration. **Switch** is the setting that controls it.

## A2A protocol

What the adapter offers to A2A clients.

| Id | What | State | Default | Switch | Evidence |
|---|---|---|---|---|---|
| `a2a.binding.http_json` | HTTP+JSON (REST) binding | supported | on | - | `tck:official`<br>`interop:clients`<br>`tests/test_protocol_version.py::test_rest_routes_resolve_at_base_and_legacy_prefix` |
| `a2a.binding.jsonrpc` | JSON-RPC binding | supported | on | - | `tck:official`<br>`interop:clients`<br>`tests/test_protocol_version.py::test_agent_card_has_jsonrpc_and_http_json_bindings` |
| `a2a.parts.text` | Text parts | supported | on | - | `tests/test_executor.py::test_executor_emits_task_and_completion_events` |
| `a2a.protocol.1_0` | A2A protocol 1.0 | supported | on | - | `tck:official`<br>`tests/test_protocol_version.py::test_agent_card_advertises_a2a_v1` |
| `a2a.streaming` | Streaming (SSE) responses<br><sub>A core feature: switching it off changes the Agent Card and the streaming methods answer 'unsupported'.</sub> | supported | on | `LEMONADE_A2A_FEATURES=-a2a.streaming` | `tck:official`<br>`tests/test_executor.py::test_executor_emits_task_and_completion_events`<br>`tests/test_live_lifecycle.py::test_disconnect_does_not_cancel_by_default` |
| `a2a.task.cancel` | CancelTask | supported | on | - | `tck:official`<br>`tests/test_cancellation.py::test_executor_tracks_and_cancels_active_coroutine`<br>`tests/test_live_lifecycle.py::test_repeated_cancel_races_leave_no_task_running` |
| `a2a.task.list` | ListTasks / GetTask | supported | on | - | `tck:official`<br>`tests/test_identity_and_limits.py::test_users_cannot_read_list_or_cancel_each_others_tasks` |
| `a2a.task.subscribe` | SubscribeToTask | supported | on | - | `tck:official`<br>`tests/test_live_lifecycle.py::test_cancel_on_disconnect_ignores_a_subscriber_leaving` |
| `a2a.binding.grpc` | gRPC binding<br><sub>Decision recorded in docs/conformance.md: no consumer, adds a dependency and a second listener.</sub> | unsupported | off | - | - |
| `a2a.extended_agent_card` | Extended Agent Card<br><sub>No authenticated-only information to expose.</sub> | unsupported | off | - | - |
| `a2a.parts.data` | Structured data parts | unsupported | off | - | `tests/test_safe_urls.py::test_file_url_and_data_parts_are_refused_on_both_bindings` |
| `a2a.parts.file` | File parts<br><sub>Rejected with CONTENT_TYPE_NOT_SUPPORTED; the evidence shows the rejection.</sub> | unsupported | off | - | `tests/test_safe_urls.py::test_file_url_and_data_parts_are_refused_on_both_bindings` |
| `a2a.parts.url` | URL parts | unsupported | off | - | `tests/test_safe_urls.py::test_file_url_and_data_parts_are_refused_on_both_bindings` |
| `a2a.protocol.0_3_compat` | A2A 0.3 compatibility layer<br><sub>The SDK's v0.3 compatibility routes are disabled; 0.3 clients are not served.</sub> | unsupported | off | - | - |
| `a2a.push_notifications` | Push notifications<br><sub>Needs the URL policy in safe_urls.py wired in first (SSRF).</sub> | unsupported | off | - | - |

## Adapter

What `lemonade-a2a` itself provides and how to switch it.

| Id | What | State | Default | Switch | Evidence |
|---|---|---|---|---|---|
| `adapter.auth.api_key` | API-key authentication | supported | off | `LEMONADE_A2A_API_KEY` | `tests/test_auth.py::test_bearer_and_x_api_key_are_accepted` |
| `adapter.auth.mtls` | Mutual TLS | supported | off | `LEMONADE_A2A_SSL_REQUIRE_CLIENT_CERT` | `tests/test_mtls.py::test_a_client_with_a_certificate_from_our_ca_is_served`<br>`tests/test_mtls.py::test_a_client_without_a_certificate_cannot_connect` |
| `adapter.auth.multi_key` | Named API keys (per-caller identity) | supported | off | `LEMONADE_A2A_API_KEYS` | `tests/test_identity_and_limits.py::test_each_key_is_its_own_user` |
| `adapter.backpressure` | Slow-consumer backpressure<br><sub>Provided by the A2A SDK's bounded event queue; the bound is not configurable here.</sub> | supported | on | - | `tests/test_live_lifecycle.py::test_stalled_streaming_client_cannot_wedge_the_adapter` |
| `adapter.cancel_on_disconnect` | Cancel a task when its streaming client disconnects | supported | off | `LEMONADE_A2A_CANCEL_ON_DISCONNECT` | `tests/test_live_lifecycle.py::test_cancel_on_disconnect_cancels_the_task`<br>`script:benchmarks/disconnect_proof.py` |
| `adapter.capabilities_endpoint` | Authenticated capabilities endpoint and optional Agent Card extension | supported | off | `LEMONADE_A2A_EXPOSE_CAPABILITIES` | `tests/test_cli_and_compat.py::test_capabilities_endpoint_describes_the_adapter`<br>`tests/test_cli_and_compat.py::test_card_points_at_it_with_an_optional_extension` |
| `adapter.compat_modes` | Startup compatibility gate (off, warn, strict)<br><sub>Active here means strict; warn is the default.</sub> | supported | off | `LEMONADE_A2A_COMPAT` | `tests/test_cli_and_compat.py::test_strict_mode_refuses_to_start_on_a_failed_check`<br>`tests/test_cli_and_compat.py::test_warn_mode_logs_and_starts` |
| `adapter.config_file` | Layered configuration (defaults, TOML file, environment, command line) | supported | on | `LEMONADE_A2A_CONFIG` | `tests/test_config_layers.py::test_each_layer_overrides_the_one_below`<br>`tests/test_cli_and_compat.py::test_config_show_reports_sources` |
| `adapter.doctor` | `lemonade-a2a doctor`: cross-validate versions, features and the backend | supported | on | - | `tests/test_cli_and_compat.py::test_doctor_json_with_backend_and_adapter`<br>`tests/test_cli_and_compat.py::test_doctor_exit_code_when_lemonade_is_down`<br>`tests/test_cli_and_compat.py::test_sdk_check` |
| `adapter.logging.json` | Structured JSON logs with trace ids | supported | off | `LEMONADE_A2A_LOG_FORMAT=json` | `tests/test_telemetry.py::test_log_lines_carry_trace_ids_and_no_prompt_text` |
| `adapter.otel` | OpenTelemetry traces, metrics and logs | supported | off | `LEMONADE_A2A_OTEL` | `tests/test_telemetry.py::test_a_request_produces_a_connected_trace`<br>`tests/test_telemetry_setup.py::test_an_unreachable_collector_does_not_slow_or_fail_requests` |
| `adapter.otel.capture_content` | Record prompt and response text in telemetry<br><sub>Off by default; the external profile refuses it.</sub> | supported | off | `LEMONADE_A2A_OTEL_CAPTURE=content` | `tests/test_telemetry.py::test_content_mode_records_truncated_text_as_events`<br>`tests/test_telemetry.py::test_prompts_never_appear_in_exported_telemetry` |
| `adapter.otel.prometheus` | Prometheus /metrics endpoint | supported | off | `LEMONADE_A2A_OTEL_PROMETHEUS` | `tests/test_telemetry_setup.py::test_metrics_endpoint_serves_the_adapter_instruments` |
| `adapter.plugins` | Extension API v1: backends, task stores, authenticators, telemetry | supported | on | `LEMONADE_A2A_BACKEND / _TASK_STORE / _AUTHENTICATOR / _TELEMETRY_PLUGIN` | `tests/test_plugins_and_stores.py::test_a_backend_plugin_serves_real_requests`<br>`tests/test_plugins_and_stores.py::test_an_authenticator_plugin_decides_who_is_calling`<br>`tests/test_plugins_and_stores.py::test_a_plugin_built_for_another_api_version_is_refused` |
| `adapter.profile` | Exposure profiles (local, lan, external) | supported | on | `LEMONADE_A2A_PROFILE` | `tests/test_identity_and_limits.py::test_external_profile_needs_auth_tls_and_rate_limit`<br>`tests/test_identity_and_limits.py::test_local_profile_refuses_a_public_bind` |
| `adapter.ratelimit` | Per-identity rate limiting | supported | off | `LEMONADE_A2A_RATE_LIMIT_PER_MINUTE` | `tests/test_identity_and_limits.py::test_rate_limit_answers_429_with_retry_after_per_identity` |
| `adapter.reasoning.artifact` | Stream a reasoning model's thinking as a separate artifact | supported | off | `LEMONADE_A2A_REASONING=artifact` | `tests/test_reasoning_and_stream_errors.py::test_reasoning_can_be_streamed_as_a_separate_artifact` |
| `adapter.reasoning.drop` | Drop a reasoning model's thinking (default) | supported | on | `LEMONADE_A2A_REASONING` | `tests/test_reasoning_and_stream_errors.py::test_reasoning_is_dropped_by_default`<br>`tests/test_reasoning_and_stream_errors.py::test_all_reasoning_and_no_answer_fails_the_task` |
| `adapter.resource_bounds` | Input, deadline, concurrency and store bounds | supported | on | - | `tests/test_executor.py::test_excess_concurrent_tasks_are_rejected`<br>`tests/test_executor.py::test_task_deadline_fails_the_task`<br>`tests/test_task_store.py::test_oldest_finished_tasks_are_evicted_first` |
| `adapter.support_bundle` | `lemonade-a2a support-bundle`: redacted diagnostics archive | supported | on | - | `tests/test_cli_and_compat.py::test_support_bundle_is_redacted` |
| `adapter.task_isolation` | Per-user task isolation | supported | off | - | `tests/test_identity_and_limits.py::test_users_cannot_read_list_or_cancel_each_others_tasks`<br>`tests/test_identity_and_limits.py::test_a_user_cannot_continue_another_users_task` |
| `adapter.task_store.sqlite` | Persistent tasks in SQLite (survive restarts, bounded)<br><sub>Needs the sqlite extra. A task that was running at shutdown is reported FAILED on first read.</sub> | supported | off | `LEMONADE_A2A_TASK_STORE=sqlite` | `tests/test_plugins_and_stores.py::test_tasks_survive_a_restart`<br>`tests/test_plugins_and_stores.py::test_a_task_that_was_running_at_shutdown_is_reported_failed`<br>`tests/test_plugins_and_stores.py::test_the_oldest_finished_tasks_are_evicted_and_running_ones_never` |
| `adapter.tls` | TLS | supported | off | `LEMONADE_A2A_SSL_CERTFILE` | `tests/test_hardening.py::test_main_forwards_tls_files` |

## Lemonade behaviour

What the adapter relies on from Lemonade Server.

| Id | What | State | Default | Switch | Evidence |
|---|---|---|---|---|---|
| `lemonade.cancel_by_disconnect` | Closing the connection stops generation<br><sub>Measured on real Lemonade: a queued small request is served in ~177 ms after CancelTask.</sub> | supported | on | - | `script:benchmarks/cancellation_proof.py`<br>`script:benchmarks/disconnect_proof.py` |
| `lemonade.in_stream_errors` | Errors reported inside a 200 stream<br><sub>Seen when a prompt exceeds the loaded model's context window.</sub> | supported | on | - | `tests/test_reasoning_and_stream_errors.py::test_an_error_event_inside_a_200_stream_fails_the_task` |
| `lemonade.reasoning_content` | reasoning_content deltas from reasoning models | supported | on | - | `tests/test_reasoning_and_stream_errors.py::test_client_separates_reasoning_from_the_answer` |
| `lemonade.stream.sse` | OpenAI-compatible SSE chat streaming | supported | on | - | `tests/test_lemonade_client.py::test_stream_yields_only_text_deltas`<br>`script:scripts/real_lemonade_e2e.py` |
| `lemonade.context_window` | Model context window sized from available memory<br><sub>Gemma-4-12B on an 8 GB GPU loads with a 1,459-token context; /v1/models reports context_length.</sub> | observed | - | - | - |
| `lemonade.health_version` | Server version in /api/v1/health<br><sub>Read by `doctor`; the `lemonade --version` CLI is the fallback.</sub> | observed | - | - | - |
| `lemonade.otlp_telemetry` | Lemonade's own OTLP traces, with an option to trust the incoming trace context<br><sub>Seen in Lemonade 2026.40.0 defaults (telemetry.trust_incoming_trace_context, default false). The adapter forwards traceparent either way.</sub> | observed | - | - | - |
| `lemonade.prometheus_metrics` | Lemonade's own /metrics endpoint<br><sub>Observed on 2026.40.0 (lemonade_server_up, build info); join with adapter metrics by instance label.</sub> | observed | - | - | - |
| `lemonade.usage_reporting` | Token usage in the stream<br><sub>Not present in the streams observed; token metrics are recorded only if it appears.</sub> | unknown | - | - | - |

## A2A SDK

What was observed in the `a2a-sdk` it is built on.

| Id | What | State | Default | Switch | Evidence |
|---|---|---|---|---|---|
| `sdk.otel_hooks` | The A2A SDK emits OpenTelemetry spans<br><sub>Whenever opentelemetry is installed and a provider is configured; OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false turns it off.</sub> | observed | - | - | - |
| `sdk.request_handler_v2` | SDK request handler v2 ignores a custom queue manager<br><sub>Why the event-queue bound is not configurable here.</sub> | observed | - | - | - |
