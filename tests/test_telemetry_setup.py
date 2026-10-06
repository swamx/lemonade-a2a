"""Exporter configuration, resilience (a broken collector must not hurt requests) and /metrics."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from lemonade_a2a import telemetry as telemetry_module
from lemonade_a2a.config import Settings
from lemonade_a2a.executor import LemonadeAgentExecutor
from lemonade_a2a.server import create_app
from lemonade_a2a.telemetry import Telemetry, TelemetryConfigError, setup_telemetry

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}

OTEL_VARS = (
    "OTEL_TRACES_EXPORTER",
    "OTEL_METRICS_EXPORTER",
    "OTEL_LOGS_EXPORTER",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TIMEOUT",
    "OTEL_TRACES_SAMPLER",
    "OTEL_TRACES_SAMPLER_ARG",
    "OTEL_SERVICE_NAME",
    "OTEL_SDK_DISABLED",
    "OTEL_RESOURCE_ATTRIBUTES",
    "OTEL_BSP_SCHEDULE_DELAY",
)


@pytest.fixture(autouse=True)
def clean_otel_env(monkeypatch):
    for name in OTEL_VARS:
        monkeypatch.delenv(name, raising=False)


class Echo:
    async def stream(self, messages):
        yield "ok"


def _send(client: TestClient, text: str = "hi") -> None:
    body = {
        "message": {
            "messageId": f"m-{time.perf_counter_ns()}",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
    }
    response = client.post("/message:send", headers=HEADERS, content=json.dumps(body))
    assert response.status_code == 200


def _app(telemetry: Telemetry, **kwargs) -> TestClient:
    executor = LemonadeAgentExecutor(Echo(), telemetry=telemetry)
    return TestClient(create_app(Settings(**kwargs), executor=executor, telemetry=telemetry))


# --- configuration ---------------------------------------------------------------------


def test_off_by_default_builds_nothing() -> None:
    assert setup_telemetry(Settings()).enabled is False


def test_otel_sdk_disabled_wins(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")

    assert setup_telemetry(Settings(otel_enabled=True)).enabled is False


def test_none_exporters_still_give_working_instruments(monkeypatch) -> None:
    for signal in ("TRACES", "METRICS"):
        monkeypatch.setenv(f"OTEL_{signal}_EXPORTER", "none")

    telemetry = setup_telemetry(Settings(otel_enabled=True))

    assert telemetry.enabled
    with telemetry.span("x"):
        pass
    telemetry.shutdown()


def test_console_exporter_writes_spans(monkeypatch) -> None:
    import io as _io

    import opentelemetry.sdk.trace.export as export

    sink = _io.StringIO()

    class Console(export.ConsoleSpanExporter):  # the stock one binds sys.stdout at import time
        def __init__(self):
            super().__init__(out=sink)

    monkeypatch.setattr(export, "ConsoleSpanExporter", Console)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    telemetry = setup_telemetry(Settings(otel_enabled=True))

    _send(_app(telemetry))
    time.sleep(0.3)  # the task span ends just after the response
    telemetry.shutdown()

    assert "a2a.task.execute" in sink.getvalue()


@pytest.mark.parametrize("signal", ["TRACES", "METRICS", "LOGS"])
def test_unknown_exporters_are_a_clear_error(monkeypatch, signal) -> None:
    monkeypatch.setenv(f"OTEL_{signal}_EXPORTER", "carrier-pigeon")

    with pytest.raises(TelemetryConfigError, match="unsupported"):
        setup_telemetry(Settings(otel_enabled=True))


@pytest.mark.parametrize("protocol", ["http/protobuf", "grpc"])
def test_otlp_exporters_for_both_protocols_can_be_built(monkeypatch, protocol) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", protocol)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")

    telemetry = setup_telemetry(Settings(otel_enabled=True))

    assert telemetry.enabled
    telemetry.shutdown(1)


def test_a_missing_sdk_is_reported_not_swallowed(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("opentelemetry.sdk"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(TelemetryConfigError, match=r"lemonade-a2a\[otel\]"):
        setup_telemetry(Settings(otel_enabled=True))


def test_service_name_and_resource_attributes_follow_the_standard_variables(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "kitchen-llm")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=lab")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")

    telemetry = setup_telemetry(Settings(otel_enabled=True))

    attributes = dict(telemetry.runtime.tracer_provider.resource.attributes)
    assert attributes["service.name"] == "kitchen-llm"
    assert attributes["deployment.environment"] == "lab"
    assert attributes["service.version"]


def test_the_standard_sampler_variable_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    telemetry = setup_telemetry(Settings(otel_enabled=True))

    with telemetry.span("sampled out") as span:
        assert span.is_recording() is False
    telemetry.shutdown()


# --- resilience ------------------------------------------------------------------------


def test_an_unreachable_collector_does_not_slow_or_fail_requests(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")  # nothing listens
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", "100")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    telemetry = setup_telemetry(Settings(otel_enabled=True, otel_buffer=64))
    client = _app(telemetry)

    start = time.perf_counter()
    for _ in range(40):
        _send(client)
    elapsed = time.perf_counter() - start

    assert elapsed < 10, f"requests were held up by the exporter ({elapsed:.1f}s)"
    begin = time.perf_counter()
    telemetry.shutdown(2)
    assert time.perf_counter() - begin < 8, "shutdown must be bounded"


class _Raising(SpanExporter):
    def export(self, spans):
        raise RuntimeError("exporter bug")

    def shutdown(self):
        pass


class _Slow(SpanExporter):
    exported = 0

    def export(self, spans):
        time.sleep(0.5)
        type(self).exported += len(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def _with_exporter(exporter: SpanExporter, queue: int) -> Telemetry:
    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter, max_queue_size=queue, max_export_batch_size=queue, schedule_delay_millis=50
        )
    )
    return Telemetry(tracer=provider.get_tracer("t"), meter=None)


def test_an_exporter_that_raises_never_reaches_a_request() -> None:
    client = _app(_with_exporter(_Raising(), queue=16))

    for _ in range(20):
        _send(client)


def test_a_slow_exporter_drops_spans_instead_of_blocking() -> None:
    _Slow.exported = 0
    client = _app(_with_exporter(_Slow(), queue=8))

    start = time.perf_counter()
    for _ in range(60):
        _send(client)
    elapsed = time.perf_counter() - start

    assert elapsed < 8, f"a slow exporter held requests up ({elapsed:.1f}s)"
    # 60 requests make well over 100 spans; the queue holds 8, the rest were dropped.
    assert _Slow.exported < 100


# --- Prometheus pull endpoint -----------------------------------------------------------


def _prometheus_app(**kwargs):
    telemetry = setup_telemetry(Settings(otel_enabled=True, otel_prometheus=True, **kwargs))
    return telemetry, _app(telemetry, otel_enabled=True, otel_prometheus=True, **kwargs)


def test_metrics_endpoint_serves_the_adapter_instruments(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    telemetry, client = _prometheus_app()
    _send(client)

    body = client.get("/metrics").text

    assert "lemonade_a2a_task_completed_total" in body
    assert 'state="completed"' in body
    assert "http_server_request_duration" in body
    telemetry.shutdown()


def test_metrics_endpoint_is_authenticated_when_keys_are_set(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    telemetry, client = _prometheus_app(api_key="k")

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer k"}).status_code == 200
    telemetry.shutdown()


def test_two_apps_in_one_process_do_not_share_a_prometheus_registry(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    first, one = _prometheus_app()
    second, two = _prometheus_app()
    _send(one)

    assert "lemonade_a2a_task_completed_total" in one.get("/metrics").text
    assert "lemonade_a2a_task_completed_total" not in two.get("/metrics").text
    first.shutdown()
    second.shutdown()


def test_no_metrics_route_unless_enabled() -> None:
    assert _app(Telemetry.noop()).get("/metrics").status_code == 404


# --- profile rules for telemetry ----------------------------------------------------------


_EXTERNAL = {
    "profile": "external",
    "host": "0.0.0.0",
    "api_key": "k",
    "ssl_certfile": "c",
    "ssl_keyfile": "k",
    "rate_limit_per_minute": 60,
}


def test_external_profile_refuses_content_capture() -> None:
    with pytest.raises(ValueError, match="content"):
        Settings(**_EXTERNAL, otel_enabled=True, otel_capture="content")
    Settings(**_EXTERNAL, otel_enabled=True, otel_capture="metadata")
    Settings(otel_enabled=True, otel_capture="content")  # fine on loopback


def test_external_profile_refuses_plaintext_otlp_unless_accepted(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.example.com:4318")

    with pytest.raises(ValueError, match="TLS OTLP"):
        Settings(**_EXTERNAL, otel_enabled=True)
    Settings(**_EXTERNAL, otel_enabled=True, otel_allow_plaintext=True)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.example.com:4318")
    Settings(**_EXTERNAL, otel_enabled=True)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    Settings(**_EXTERNAL, otel_enabled=True)  # a local collector is fine


def test_capture_and_log_format_are_validated() -> None:
    with pytest.raises(ValueError, match="CAPTURE"):
        Settings(otel_capture="everything")
    with pytest.raises(ValueError, match="LOG_FORMAT"):
        Settings(log_format="xml")


def test_settings_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("LEMONADE_A2A_OTEL", "1")
    monkeypatch.setenv("LEMONADE_A2A_OTEL_CAPTURE", "METADATA")
    monkeypatch.setenv("LEMONADE_A2A_OTEL_REDACT", "a2a.*,http.route")
    monkeypatch.setenv("LEMONADE_A2A_OTEL_BUFFER", "100")
    monkeypatch.setenv("LEMONADE_A2A_LOG_FORMAT", "json")
    monkeypatch.setenv("LEMONADE_A2A_REASONING", "artifact")

    settings = Settings.from_env()

    assert settings.otel_enabled and settings.otel_capture == "metadata"
    assert settings.otel_redact == "a2a.*,http.route" and settings.otel_buffer == 100
    assert settings.log_format == "json" and settings.reasoning == "artifact"


def test_module_level_noop_is_shared_and_disabled() -> None:
    assert telemetry_module.NOOP.enabled is False
