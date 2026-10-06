"""OpenTelemetry for the adapter: opt-in, private by default, never able to hurt a request.

The rest of the code calls the small :class:`Telemetry` facade and never imports the
OpenTelemetry SDK. With telemetry off (the default) every method returns immediately.
Only ``opentelemetry-api`` is needed to import this module; the SDK and exporters come
with the ``[otel]`` extra and are imported lazily by :func:`setup_telemetry`.

Design and the instrument list: docs/observability.md.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.metrics import Observation
from opentelemetry.trace import Link, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from . import __version__

LOG = logging.getLogger("lemonade_a2a.telemetry")

INSTRUMENTATION_NAME = "lemonade_a2a"
_PROPAGATOR = TraceContextTextMapPropagator()  # W3C traceparent/tracestate only; baggage is ignored
_MAX_REMEMBERED_TASKS = 2048


class TelemetryConfigError(ValueError):
    """Telemetry was requested but cannot be set up (missing extra, bad exporter name)."""


@dataclass
class Telemetry:
    """Facade over one tracer and one meter. Disabled (no-op) when built without them."""

    tracer: Any = None
    meter: Any = None
    capture: str = "none"
    content_max_chars: int = 1024
    redact: tuple[str, ...] = ()
    runtime: Any = None
    _task_contexts: OrderedDict = field(default_factory=OrderedDict)
    _backend_up: int = 1
    _store_size: Callable[[], int] | None = None

    def __post_init__(self) -> None:
        self.enabled = self.tracer is not None
        self._instruments: dict[str, Any] = {}
        if self.meter is not None:
            self._create_instruments()

    # ----- construction -------------------------------------------------------------

    @classmethod
    def noop(cls) -> Telemetry:
        return cls()

    def _create_instruments(self) -> None:
        m = self.meter
        i = self._instruments
        i["http_duration"] = m.create_histogram(
            "http.server.request.duration", unit="s", description="HTTP server request duration"
        )
        i["task_duration"] = m.create_histogram(
            "lemonade_a2a.task.duration", unit="s", description="Task execution time"
        )
        i["task_active"] = m.create_up_down_counter(
            "lemonade_a2a.task.active", description="Tasks currently executing"
        )
        i["task_completed"] = m.create_counter(
            "lemonade_a2a.task.completed", description="Tasks by terminal state"
        )
        i["task_rejected"] = m.create_counter(
            "lemonade_a2a.task.rejected", description="Requests turned away, by reason"
        )
        i["ttft"] = m.create_histogram(
            "lemonade_a2a.stream.ttft", unit="s", description="Time to the first answer chunk"
        )
        i["chunk_interval"] = m.create_histogram(
            "lemonade_a2a.stream.chunk_interval", unit="s", description="Gap between chunks"
        )
        i["backend_errors"] = m.create_counter(
            "lemonade_a2a.backend.errors", description="Failed Lemonade requests, by class"
        )
        i["disconnects"] = m.create_counter(
            "lemonade_a2a.disconnects", description="Client disconnects from a streaming task"
        )
        i["evictions"] = m.create_counter(
            "lemonade_a2a.store.evictions", description="Finished tasks evicted from the store"
        )
        i["genai_duration"] = m.create_histogram(
            "gen_ai.client.operation.duration", unit="s", description="Lemonade call duration"
        )
        i["genai_tokens"] = m.create_histogram(
            "gen_ai.client.token.usage", unit="{token}", description="Tokens, if Lemonade reports"
        )
        m.create_observable_gauge(
            "lemonade_a2a.backend.up",
            callbacks=[lambda _: [Observation(self._backend_up)]],
            description="1 when the last Lemonade request succeeded, 0 when it failed",
        )
        m.create_observable_gauge(
            "lemonade_a2a.store.tasks",
            callbacks=[lambda _: [Observation(self._store_size())] if self._store_size else []],
            description="Tasks held by the store",
        )

    # ----- attributes ---------------------------------------------------------------

    def attrs(self, values: dict[str, Any]) -> dict[str, Any]:
        """Drop redacted keys (patterns allowed) and ``None`` values."""
        return {
            key: value
            for key, value in values.items()
            if value is not None and not any(fnmatch.fnmatch(key, pat) for pat in self.redact)
        }

    @property
    def captures_metadata(self) -> bool:
        return self.capture in ("metadata", "content")

    @property
    def captures_content(self) -> bool:
        return self.capture == "content"

    def identity(self, name: str) -> str:
        """Stable, non-reversible handle for an API-key identity (metadata mode)."""
        return hashlib.sha256(name.encode()).hexdigest()[:12]

    # ----- tracing ------------------------------------------------------------------

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
        links: list[Link] | None = None,
        parent_context: Any = None,
    ) -> Iterator[Any]:
        if not self.enabled:
            yield trace.INVALID_SPAN
            return
        with self.tracer.start_as_current_span(
            name,
            kind=kind,
            attributes=self.attrs(attributes or {}),
            links=links or [],
            context=parent_context,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            yield span

    def start_server_span(self, name: str, headers: dict[str, str], attributes: dict[str, Any]):
        """Server span continuing an incoming W3C trace context; returns ``(span, links)``."""
        parent = _PROPAGATOR.extract(headers)
        return self.tracer.start_span(
            name,
            context=parent,
            kind=SpanKind.SERVER,
            attributes=self.attrs(attributes),
        )

    def event(self, span: Any, name: str, **attributes: Any) -> None:
        if self.enabled and span is not None and span.is_recording():
            span.add_event(name, self.attrs(attributes))

    def fail(self, span: Any, description: str) -> None:
        """Mark a span failed with the *sanitized* text shown to clients, never the raw error."""
        if self.enabled and span is not None and span.is_recording():
            span.set_status(Status(StatusCode.ERROR, description))

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        """A child of the current span that is *not* made current: safe across ``yield``
        in an async generator, where a context-managed span could detach in the wrong task."""
        if not self.enabled:
            return trace.INVALID_SPAN
        return self.tracer.start_span(name, kind=kind, attributes=self.attrs(attributes or {}))

    def inject(self, headers: dict[str, str], span: Any = None) -> None:
        """Forward a trace context (W3C) on an outgoing request."""
        if self.enabled:
            context = trace.set_span_in_context(span) if span is not None else None
            _PROPAGATOR.inject(headers, context=context)

    # ----- task <-> later request links ---------------------------------------------

    def remember_task(self, task_id: str, span_context: Any) -> None:
        if not self.enabled or not span_context.is_valid:
            return
        self._task_contexts[task_id] = span_context
        while len(self._task_contexts) > _MAX_REMEMBERED_TASKS:
            self._task_contexts.popitem(last=False)

    def link_for_task(self, task_id: str) -> list[Link]:
        context = self._task_contexts.get(task_id)
        return [Link(context)] if context is not None else []

    # ----- metrics (all low cardinality) --------------------------------------------

    def _record(self, name: str, method: str, value: float, attributes: dict[str, Any]) -> None:
        if self.enabled and self.meter is not None:
            getattr(self._instruments[name], method)(value, self.attrs(attributes))

    def http_request(self, method: str, route: str, status: int, seconds: float) -> None:
        self._record(
            "http_duration",
            "record",
            seconds,
            {
                "http.request.method": method,
                "http.route": route,
                "http.response.status_code": status,
            },
        )

    def task_started(self) -> None:
        self._record("task_active", "add", 1, {})

    def task_finished(self, state: str, seconds: float) -> None:
        self._record("task_active", "add", -1, {})
        self._record("task_completed", "add", 1, {"state": state})
        self._record("task_duration", "record", seconds, {"state": state})

    def rejected(self, reason: str) -> None:
        self._record("task_rejected", "add", 1, {"reason": reason})

    def first_chunk(self, seconds: float) -> None:
        self._record("ttft", "record", seconds, {})

    def chunk_interval(self, seconds: float) -> None:
        self._record("chunk_interval", "record", seconds, {})

    def backend_error(self, error_class: str) -> None:
        self._backend_up = 0
        self._record("backend_errors", "add", 1, {"class": error_class})

    def backend_ok(self) -> None:
        self._backend_up = 1

    def disconnect(self, policy: str) -> None:
        self._record("disconnects", "add", 1, {"policy": policy})

    def eviction(self) -> None:
        self._record("evictions", "add", 1, {})

    def backend_call(self, seconds: float, model: str, error: str | None = None) -> None:
        self._record(
            "genai_duration",
            "record",
            seconds,
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model or None,
                "error.type": error,
            },
        )

    def token_usage(self, model: str, kind: str, tokens: int) -> None:
        self._record(
            "genai_tokens",
            "record",
            tokens,
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model or None,
                "gen_ai.token.type": kind,
            },
        )

    def bind_store(self, size: Callable[[], int]) -> None:
        self._store_size = size

    # ----- lifecycle ----------------------------------------------------------------

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        if self.runtime is not None:
            self.runtime.shutdown(timeout_seconds)

    @contextmanager
    def timed(self) -> Iterator[Callable[[], float]]:
        start = time.perf_counter()
        yield lambda: time.perf_counter() - start


NOOP = Telemetry.noop()


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def route_template(path: str) -> str:
    """Low-cardinality route for metrics and span names: ids and tenants are collapsed."""
    if path.startswith("/a2a/rest"):
        path = path[len("/a2a/rest") :] or "/"
    if path in ("/", "/a2a/jsonrpc", "/healthz", "/metrics", "/message:send", "/message:stream"):
        return path
    if path in ("/tasks", "/extendedAgentCard", "/.well-known/agent-card.json"):
        return path
    if path.startswith("/.well-known/"):
        return path
    parts = path.strip("/").split("/")
    if parts and parts[0] == "tasks" and len(parts) >= 2:
        rest = parts[1:]
        _, _, action = rest[0].partition(":")
        tail = ("/" + "/".join("{x}" for _ in rest[1:])) if len(rest) > 1 else ""
        return "/tasks/{id}" + (f":{action}" if action else "") + tail
    return "/{tenant}/..." if len(parts) > 1 else "other"


def task_id_from_path(path: str) -> str | None:
    path = path.removeprefix("/a2a/rest")
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "tasks":
        return parts[1].partition(":")[0] or None
    return None


# ------------------------------------------------------------------------------------
# SDK wiring (lazy: only imported when telemetry is switched on)
# ------------------------------------------------------------------------------------


@dataclass
class Runtime:
    """Owns the SDK providers so they can be flushed at shutdown."""

    tracer_provider: Any = None
    meter_provider: Any = None
    logger_provider: Any = None
    log_handler: Any = None
    prometheus: bool = False
    prometheus_registry: Any = None  # private registry: several apps can coexist in one process

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        if self.log_handler is not None:
            logging.getLogger("lemonade_a2a").removeHandler(self.log_handler)
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is None:
                continue
            try:
                provider.force_flush(int(timeout_seconds * 1000))
                provider.shutdown()
            except Exception:
                LOG.warning("telemetry shutdown failed", exc_info=True)


def _names(signal: str, default: str = "otlp") -> list[str]:
    raw = os.environ.get(f"OTEL_{signal}_EXPORTER", default)
    return [
        name.strip().lower() for name in raw.split(",") if name.strip() and name.strip() != "none"
    ]


def _otlp_protocol(signal: str) -> str:
    return (
        os.environ.get(f"OTEL_EXPORTER_OTLP_{signal}_PROTOCOL")
        or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "http/protobuf"
    ).lower()


def _import_otlp(signal: str):
    module = {"TRACES": "trace_exporter", "METRICS": "metric_exporter", "LOGS": "_log_exporter"}[
        signal
    ]
    cls = {
        "TRACES": "OTLPSpanExporter",
        "METRICS": "OTLPMetricExporter",
        "LOGS": "OTLPLogExporter",
    }[signal]
    flavour = "grpc" if _otlp_protocol(signal) == "grpc" else "http"
    try:
        mod = __import__(f"opentelemetry.exporter.otlp.proto.{flavour}.{module}", fromlist=[cls])
    except ImportError as exc:
        raise TelemetryConfigError(
            f"OTLP/{flavour} exporter is not installed; install lemonade-a2a[otel]"
        ) from exc
    return getattr(mod, cls)


def setup_telemetry(settings, *, set_global: bool = False) -> Telemetry:
    """Build providers and exporters from ``OTEL_*`` variables and the adapter's settings."""
    if not settings.otel_enabled or os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        return Telemetry.noop()
    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            ConsoleMetricExporter,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError as exc:
        raise TelemetryConfigError(
            "LEMONADE_A2A_OTEL=1 needs the OpenTelemetry SDK; install lemonade-a2a[otel]"
        ) from exc

    # Defaults only where the standard variables do not already say: attributes passed to
    # Resource.create() would override OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES.
    env_attributes = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
    defaults: dict[str, str] = {}
    if not os.environ.get("OTEL_SERVICE_NAME") and "service.name=" not in env_attributes:
        defaults["service.name"] = "lemonade-a2a"
    if "service.version=" not in env_attributes:
        defaults["service.version"] = __version__
    resource = Resource.create(defaults)
    runtime = Runtime()

    tracer_provider = TracerProvider(resource=resource)
    for name in _names("TRACES"):
        exporter = (
            ConsoleSpanExporter()
            if name == "console"
            else _import_otlp("TRACES")()
            if name == "otlp"
            else None
        )
        if exporter is None:
            raise TelemetryConfigError(f"unsupported OTEL_TRACES_EXPORTER: {name}")
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=settings.otel_buffer,
                max_export_batch_size=min(512, settings.otel_buffer),
            )
        )
    runtime.tracer_provider = tracer_provider

    readers = []
    for name in _names("METRICS"):
        if name == "console":
            readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))
        elif name == "otlp":
            readers.append(PeriodicExportingMetricReader(_import_otlp("METRICS")()))
        elif name == "prometheus":
            runtime.prometheus = True
        else:
            raise TelemetryConfigError(f"unsupported OTEL_METRICS_EXPORTER: {name}")
    if settings.otel_prometheus:
        runtime.prometheus = True
    if runtime.prometheus:
        try:
            from opentelemetry.exporter.prometheus import PrometheusMetricReader
            from prometheus_client import CollectorRegistry
        except ImportError as exc:
            raise TelemetryConfigError("the Prometheus endpoint needs lemonade-a2a[otel]") from exc
        runtime.prometheus_registry = CollectorRegistry()
        readers.append(PrometheusMetricReader(registry=runtime.prometheus_registry))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    runtime.meter_provider = meter_provider

    log_names = _names("LOGS", default="none")
    if log_names:
        _setup_logs(runtime, resource, log_names, settings)

    if set_global:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)

    return Telemetry(
        tracer=tracer_provider.get_tracer(INSTRUMENTATION_NAME, __version__),
        meter=meter_provider.get_meter(INSTRUMENTATION_NAME, __version__),
        capture=settings.otel_capture,
        content_max_chars=settings.otel_content_max_chars,
        redact=tuple(p for p in settings.otel_redact.split(",") if p),
        runtime=runtime,
    )


def _setup_logs(runtime: Runtime, resource, names: list[str], settings) -> None:
    """Optional OpenTelemetry log bridge (the SDK's logs API is still experimental)."""
    try:
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogRecordExporter
    except ImportError:
        try:
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from opentelemetry.sdk._logs.export import (
                ConsoleLogExporter as ConsoleLogRecordExporter,
            )
        except ImportError as exc:
            raise TelemetryConfigError("log export needs lemonade-a2a[otel]") from exc
    provider = LoggerProvider(resource=resource)
    for name in names:
        if name == "console":
            exporter = ConsoleLogRecordExporter()
        elif name == "otlp":
            exporter = _import_otlp("LOGS")()
        else:
            raise TelemetryConfigError(f"unsupported OTEL_LOGS_EXPORTER: {name}")
        provider.add_log_record_processor(
            BatchLogRecordProcessor(
                exporter,
                max_queue_size=settings.otel_buffer,
                max_export_batch_size=min(512, settings.otel_buffer),
            )
        )
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    logging.getLogger("lemonade_a2a").addHandler(handler)
    runtime.logger_provider = provider
    runtime.log_handler = handler


def prometheus_payload(registry: Any) -> tuple[bytes, str]:
    """Body and content type for the optional ``/metrics`` route."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest(registry), CONTENT_TYPE_LATEST
