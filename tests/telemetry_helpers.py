"""In-memory OpenTelemetry for tests: spans and metrics without any exporter or network."""

from __future__ import annotations

import json
import time
from collections import defaultdict

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lemonade_a2a.telemetry import Telemetry


class Recorder:
    """A real :class:`Telemetry` wired to in-memory exporters."""

    def __init__(
        self, capture: str = "none", redact: tuple[str, ...] = (), content_max: int = 1024
    ):
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.reader = InMemoryMetricReader()
        meters = MeterProvider(metric_readers=[self.reader])
        self.telemetry = Telemetry(
            tracer=provider.get_tracer("test"),
            meter=meters.get_meter("test"),
            capture=capture,
            redact=redact,
            content_max_chars=content_max,
        )

    # ----- spans --------------------------------------------------------------------

    @property
    def spans(self):
        return list(self.exporter.get_finished_spans())

    def span(self, name: str):
        # The task span can end a moment after the HTTP response has been sent.
        deadline = time.monotonic() + 3
        while True:
            found = [s for s in self.spans if s.name == name]
            if found or time.monotonic() > deadline:
                break
            time.sleep(0.02)
        assert found, f"no span named {name!r}; have {[s.name for s in self.spans]}"
        return found[-1]

    def settle(self, seconds: float = 0.05) -> None:
        time.sleep(seconds)

    # ----- metrics ------------------------------------------------------------------

    def points(self, metric: str) -> list[tuple[dict, object]]:
        """``[(attributes, point)]`` for one instrument."""
        data = self.reader.get_metrics_data()
        out = []
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                for item in scope.metrics:
                    if item.name == metric:
                        out += [(dict(p.attributes), p) for p in item.data.data_points]
        return out

    def total(self, metric: str, **where) -> float:
        self.settle()
        total = 0.0
        for attributes, point in self.points(metric):
            if all(attributes.get(k) == v for k, v in where.items()):
                total += getattr(point, "value", getattr(point, "count", 0))
        return total

    def sum(self, metric: str, **where) -> float:
        """Sum of the recorded values of a histogram (e.g. tokens)."""
        self.settle()
        return sum(
            getattr(point, "sum", 0.0)
            for attributes, point in self.points(metric)
            if all(attributes.get(k) == v for k, v in where.items())
        )

    def series_count(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        data = self.reader.get_metrics_data()
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                for item in scope.metrics:
                    counts[item.name] += len(item.data.data_points)
        return dict(counts)

    # ----- everything that could leak text -------------------------------------------

    def everything_exported(self) -> str:
        """Spans (names, attributes, events, links) and metric attributes, as one string."""
        self.settle()
        parts = []
        for span in self.spans:
            parts.append(span.name)
            parts.append(json.dumps(dict(span.attributes or {}), default=str))
            for event in span.events:
                parts.append(event.name)
                parts.append(json.dumps(dict(event.attributes or {}), default=str))
            if span.status and span.status.description:
                parts.append(span.status.description)
        data = self.reader.get_metrics_data()
        for resource in data.resource_metrics if data else []:
            for scope in resource.scope_metrics:
                for item in scope.metrics:
                    parts.append(item.name)
                    for point in item.data.data_points:
                        parts.append(json.dumps(dict(point.attributes), default=str))
        return "\n".join(parts)
