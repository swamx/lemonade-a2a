"""Behaviour that the larger test files do not reach: logging, telemetry edges, registry rules,
the CLI's text output and the deep Lemonade probe's failure modes."""

from __future__ import annotations

import builtins
import json
import logging
import runpy
import sys
from contextlib import contextmanager

import httpx
import pytest

from lemonade_a2a import cli, compat, registry, sdk_gap, telemetry
from lemonade_a2a.compat import FAIL, UNKNOWN, WARN
from lemonade_a2a.config import Settings
from lemonade_a2a.logging_setup import JsonFormatter, configure_logging
from lemonade_a2a.telemetry import (
    NOOP,
    Runtime,
    TelemetryConfigError,
    route_template,
    setup_telemetry,
    truncate,
)
from tests.telemetry_helpers import Recorder

# --- logging --------------------------------------------------------------------------------


def _record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        "lemonade_a2a.x", logging.WARNING, __file__, 1, "hello %s", ("world",), None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_carries_extras_and_exception_types() -> None:
    try:
        raise KeyError("secret detail")
    except KeyError:
        import sys as _sys

        record = _record(event="task.failed", count=3, ok=True, nested={"a": 1})
        record.exc_info = _sys.exc_info()

    entry = json.loads(JsonFormatter().format(record))

    assert entry["message"] == "hello world" and entry["level"] == "WARNING"
    assert entry["event"] == "task.failed" and entry["count"] == 3 and entry["ok"] is True
    assert entry["nested"] == "{'a': 1}"  # non-scalars are stringified
    assert entry["exc_type"] == "KeyError" and "secret detail" not in json.dumps(entry)
    assert "trace_id" not in entry  # not inside a span


def test_configure_logging_switches_format() -> None:
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging("json")
        assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
        configure_logging("text")  # keeps existing handlers (basicConfig without force)
    finally:
        root.handlers[:] = saved


# --- telemetry edges ---------------------------------------------------------------------------


def test_remembered_tasks_are_bounded() -> None:
    recorder = Recorder()
    telemetry_ = recorder.telemetry
    with telemetry_.span("x") as span:
        context = span.get_span_context()
    for index in range(telemetry._MAX_REMEMBERED_TASKS + 50):
        telemetry_.remember_task(f"t{index}", context)

    assert len(telemetry_._task_contexts) == telemetry._MAX_REMEMBERED_TASKS
    assert telemetry_.link_for_task("t0") == [] and telemetry_.link_for_task("t60")


def test_disconnect_eviction_and_timing_helpers() -> None:
    recorder = Recorder()

    recorder.telemetry.disconnect("canceled")
    recorder.telemetry.eviction()
    with recorder.telemetry.timed() as elapsed:
        pass

    assert recorder.total("lemonade_a2a.disconnects", policy="canceled") == 1
    assert recorder.total("lemonade_a2a.store.evictions") == 1
    assert elapsed() >= 0


def test_route_templates_for_well_known_paths() -> None:
    assert (
        route_template("/.well-known/lemonade-a2a/capabilities")
        == "/.well-known/lemonade-a2a/capabilities"
    )
    assert truncate("abcdef", 3) == "abc…" and truncate("ab", 3) == "ab"


def test_runtime_shutdown_survives_a_failing_provider() -> None:
    class Bad:
        def force_flush(self, timeout):
            raise RuntimeError("boom")

        def shutdown(self):
            pass

    handler = logging.NullHandler()
    logging.getLogger("lemonade_a2a").addHandler(handler)

    Runtime(tracer_provider=Bad(), log_handler=handler).shutdown(0.1)  # must not raise

    assert handler not in logging.getLogger("lemonade_a2a").handlers


@pytest.fixture
def clean_env(monkeypatch):
    for name in (
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
        "OTEL_SDK_DISABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_console_metrics_and_prometheus_names_are_accepted(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "console,prometheus")

    result = setup_telemetry(Settings(otel_enabled=True))

    assert result.runtime.prometheus and result.runtime.prometheus_registry is not None
    result.shutdown(1)


def test_a_missing_prometheus_extra_is_reported(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("opentelemetry.exporter.prometheus"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(TelemetryConfigError, match=r"lemonade-a2a\[otel\]"):
        setup_telemetry(Settings(otel_enabled=True, otel_prometheus=True))


def test_global_providers_are_installed_only_when_asked(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    calls = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", lambda p: calls.append("trace"))
    monkeypatch.setattr(telemetry.metrics, "set_meter_provider", lambda p: calls.append("metrics"))

    setup_telemetry(Settings(otel_enabled=True)).shutdown(1)
    assert calls == []
    setup_telemetry(Settings(otel_enabled=True), set_global=True).shutdown(1)
    assert calls == ["trace", "metrics"]


def test_log_export_bridges_the_adapter_logger(clean_env, monkeypatch) -> None:
    import opentelemetry.sdk._logs.export as log_export

    seen = []

    class Capture(
        log_export.ConsoleLogRecordExporter
        if hasattr(log_export, "ConsoleLogRecordExporter")
        else log_export.ConsoleLogExporter
    ):  # type: ignore[misc]
        def export(self, batch):
            seen.extend(batch)
            return (
                log_export.LogRecordExportResult.SUCCESS
                if hasattr(log_export, "LogRecordExportResult")
                else log_export.LogExportResult.SUCCESS
            )

    name = (
        "ConsoleLogRecordExporter"
        if hasattr(log_export, "ConsoleLogRecordExporter")
        else "ConsoleLogExporter"
    )
    monkeypatch.setattr(log_export, name, Capture)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "console")

    result = setup_telemetry(Settings(otel_enabled=True))
    logging.getLogger("lemonade_a2a.test").warning("bridged line")
    result.shutdown(2)

    bodies = [str(getattr(getattr(item, "log_record", item), "body", "")) for item in seen]
    assert any("bridged line" in body for body in bodies), bodies
    assert result.runtime.log_handler is not None


def test_log_export_otlp_uses_the_otlp_exporter(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")

    result = setup_telemetry(Settings(otel_enabled=True))

    assert result.runtime.logger_provider is not None
    result.shutdown(1)


def test_missing_log_sdk_is_reported(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "console")
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("opentelemetry.sdk._logs"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(TelemetryConfigError, match="log export"):
        setup_telemetry(Settings(otel_enabled=True))


def test_the_noop_never_records() -> None:
    assert NOOP.start_span("x").is_recording() is False
    NOOP.set_compat_status(2)
    NOOP.bind_store(lambda: 1)
    assert NOOP.attrs({"a": None, "b": 1}) == {"b": 1}


# --- registry rules -------------------------------------------------------------------------------


def test_registry_validation_catches_each_kind_of_mistake() -> None:
    good = {
        "id": "adapter.x",
        "title": "X",
        "state": "supported",
        "since": "0.1.0",
        "verified_by": ["tck:official"],
    }
    bad = {
        "features": [
            good,
            dict(good),  # duplicate id
            {**good, "id": "Bad-Id"},  # malformed id
            {**good, "id": "other.thing"},  # unknown domain
            {**good, "id": "adapter.y", "state": "shiny"},
            {"id": "adapter.z", "state": "supported"},  # missing title/since/verified_by
            {**good, "id": "adapter.w", "verified_by": []},  # supported without evidence
            {**good, "id": "adapter.v", "toggle": {"setting": "x"}},
        ]
    }

    problems = "\n".join(registry.validate(bad))

    for needle in (
        "duplicate id adapter.x",
        "Bad-Id",
        "other.thing",
        "adapter.y: state must be one of",
        "missing title",
        "needs verified_by",
        "toggle needs",
    ):
        assert needle in problems, needle


def test_registry_lookups() -> None:
    assert registry.feature("a2a.streaming")["state"] == "supported"
    assert registry.feature("nope") is None
    assert registry.schema("features.schema.json")["title"]


# --- SDK gap ---------------------------------------------------------------------------------------


def test_sdk_gap_opportunities_accept_a_precomputed_list() -> None:
    gaps = [
        sdk_gap.Gap("a", "A", True, "supported", None, ""),
        sdk_gap.Gap("b", "B", True, "not used", None, ""),
        sdk_gap.Gap("c", "C", False, "not used", None, ""),
    ]

    assert [g.id for g in sdk_gap.opportunities(gaps)] == ["b"]
    assert gaps[0].as_dict()["id"] == "a"


# --- the deep probe's failure modes -------------------------------------------------------------------


def _fake_stream(lines: list[str] | Exception):
    @contextmanager
    def stream(method, url, **kwargs):
        if isinstance(lines, Exception):
            raise lines

        class Response:
            def raise_for_status(self):
                pass

            def iter_lines(self):
                return iter(lines)

        yield Response()

    return stream


def _probe(monkeypatch, lines) -> compat.Check:
    monkeypatch.setattr(compat.httpx, "stream", _fake_stream(lines))
    return compat._probe_generation(Settings(model="m"), "http://x", timeout=1)


def test_deep_probe_reasoning_only_is_a_warning(monkeypatch) -> None:
    line = 'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}'

    assert _probe(monkeypatch, [line, "data: [DONE]"]).verdict == WARN


def test_deep_probe_with_no_text_fails(monkeypatch) -> None:
    assert _probe(monkeypatch, ['data: {"choices":[{"delta":{}}]}']).verdict == FAIL


def test_deep_probe_transport_errors_fail(monkeypatch) -> None:
    assert _probe(monkeypatch, httpx.ConnectError("down")).verdict == FAIL


def test_unparseable_versions_are_not_comparable() -> None:
    assert compat._parse("not-a-version") is None and compat._parse(None) is None


def test_probe_lemonade_without_a_model_notes_the_default(monkeypatch) -> None:
    class Reply:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def get(url, **kwargs):
        if url.endswith("/api/v1/health"):
            return Reply({"status": "ok", "version": "2026.40.0"})
        return Reply({"data": [{"id": "a"}, {"id": "b"}]})

    monkeypatch.setattr(compat.httpx, "get", get)

    checks = {c.id: c for c in compat.probe_lemonade(Settings(), compat.registry.manifest())}

    assert "2 models listed" in checks["lemonade_model"].detail


def test_probe_lemonade_survives_a_broken_models_endpoint(monkeypatch) -> None:
    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(compat.httpx, "get", lambda url, **k: Reply())

    assert compat.probe_lemonade(Settings(), compat.registry.manifest())[0].verdict == UNKNOWN


# --- the CLI's text output -------------------------------------------------------------------------------


def test_config_show_text_lists_every_setting(capsys, monkeypatch) -> None:
    monkeypatch.setenv("LEMONADE_A2A_PORT", "9450")

    assert cli.main(["config", "show"]) == 0
    out = capsys.readouterr().out

    assert "port" in out and "9450" in out and "(env)" in out and "(default)" in out


def test_config_validate_prints_warnings(capsys, monkeypatch) -> None:
    monkeypatch.setenv("LEMONADE_A2A_PROFILE", "lan")
    monkeypatch.setenv("LEMONADE_A2A_HOST", "0.0.0.0")
    monkeypatch.setenv("LEMONADE_A2A_API_KEY", "k")

    assert cli.main(["config", "validate"]) == 0
    out = capsys.readouterr().out

    assert "warning" in out and "TLS" in out and "valid" in out


def test_doctor_text_marks_every_verdict(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        compat, "installed_version", lambda name: "1.2.99" if name == "a2a-sdk" else None
    )

    code = cli.main(["doctor", "--no-lemonade"])
    out = capsys.readouterr().out

    assert code == 1 and "[warn]" in out and "next:" in out and "Verdict: WARN" in out


def test_help_exits_cleanly(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])

    assert info.value.code == 0 and "doctor" in capsys.readouterr().out


def test_module_entry_point_runs_the_cli(capsys, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["lemonade-a2a", "version"])

    with pytest.raises(SystemExit) as info:
        runpy.run_module("lemonade_a2a", run_name="__main__")

    assert info.value.code == 0 and "lemonade-a2a" in capsys.readouterr().out


def test_capabilities_text_marks_switches(capsys) -> None:
    cli.main(["capabilities"])
    out = capsys.readouterr().out

    assert "off adapter.cancel_on_disconnect" in out
    assert "[LEMONADE_A2A_CANCEL_ON_DISCONNECT]" in out and "on  a2a.streaming" in out
