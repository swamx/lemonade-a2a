"""Traces, metrics, privacy modes and trace propagation (in-memory exporters)."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from opentelemetry.trace import SpanKind, StatusCode

from lemonade_a2a.config import Settings
from lemonade_a2a.executor import LemonadeAgentExecutor
from lemonade_a2a.lemonade_client import LemonadeClient
from lemonade_a2a.logging_setup import JsonFormatter
from lemonade_a2a.server import create_app
from lemonade_a2a.telemetry import NOOP, Telemetry, route_template, task_id_from_path
from tests.telemetry_helpers import Recorder

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
TRACEPARENT = f"00-{TRACE_ID}-b7ad6b7169203331-01"


class EchoClient:
    async def stream(self, messages):
        text = messages[0]["content"]
        yield "echo: "
        yield text[:20]


class BrokenClient:
    async def stream(self, messages):
        raise httpx.ConnectError("connection refused to http://secret-host:13305")
        yield  # pragma: no cover


def _app(recorder: Recorder, client=None, **kwargs) -> TestClient:
    executor = LemonadeAgentExecutor(client or EchoClient(), telemetry=recorder.telemetry)
    config = Settings(**kwargs)
    return TestClient(create_app(config, executor=executor, telemetry=recorder.telemetry))


def _send(client: TestClient, text: str = "hello", headers=None) -> dict:
    body = {
        "message": {
            "messageId": f"m-{abs(hash(text))}",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
    }
    response = client.post("/message:send", headers=headers or HEADERS, content=json.dumps(body))
    assert response.status_code == 200, response.text
    return response.json()["task"]


# --- spans ---------------------------------------------------------------------------


def test_a_request_produces_a_connected_trace() -> None:
    recorder = Recorder()
    _send(_app(recorder))

    server = recorder.span("POST /message:send")
    task = recorder.span("a2a.task.execute")

    assert server.kind == SpanKind.SERVER
    assert server.attributes["http.route"] == "/message:send"
    assert server.attributes["a2a.binding"] == "http_json"
    assert server.attributes["http.response.status_code"] == 200
    assert task.context.trace_id == server.context.trace_id  # same trace as the request
    assert task.attributes["a2a.task.state"] == "completed"
    assert task.attributes["a2a.input.chars"] == len("hello")


def test_jsonrpc_requests_are_labelled_with_their_binding() -> None:
    recorder = Recorder()
    client = _app(recorder)
    client.post(
        "/",
        headers=HEADERS,
        content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "x"}}),
    )

    assert recorder.span("POST /").attributes["a2a.binding"] == "jsonrpc"


def test_events_mark_the_first_chunk() -> None:
    recorder = Recorder()
    _send(_app(recorder))

    names = [event.name for event in recorder.span("a2a.task.execute").events]
    assert "first_chunk" in names


def test_incoming_trace_context_is_continued() -> None:
    recorder = Recorder()
    _send(_app(recorder), headers={**HEADERS, "traceparent": TRACEPARENT})

    server = recorder.span("POST /message:send")
    assert format(server.context.trace_id, "032x") == TRACE_ID
    assert format(server.parent.span_id, "016x") == "b7ad6b7169203331"
    assert format(recorder.span("a2a.task.execute").context.trace_id, "032x") == TRACE_ID


def test_trace_context_reaches_lemonade() -> None:
    recorder = Recorder()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')

    lemonade = LemonadeClient("http://lemonade/v1", "m", telemetry=recorder.telemetry)
    lemonade._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = _app(recorder, client=lemonade)

    _send(client, headers={**HEADERS, "traceparent": TRACEPARENT})

    assert seen["traceparent"].split("-")[1] == TRACE_ID
    backend = recorder.span("lemonade.chat.stream")
    assert backend.kind == SpanKind.CLIENT
    assert backend.attributes["gen_ai.operation.name"] == "chat"
    assert backend.attributes["server.address"] == "lemonade"
    assert format(backend.context.trace_id, "032x") == TRACE_ID


def test_a_later_request_about_a_task_links_to_the_span_that_ran_it() -> None:
    recorder = Recorder()
    client = _app(recorder)
    task = _send(client)

    client.get(f"/tasks/{task['id']}", headers=HEADERS)

    execute = recorder.span("a2a.task.execute")
    lookup = recorder.span("GET /tasks/{id}")
    assert [link.context.span_id for link in lookup.links] == [execute.context.span_id]
    # The task id is never in a span name or the route attribute.
    assert task["id"] not in lookup.name and task["id"] not in lookup.attributes["http.route"]


def test_a_failing_backend_marks_the_span_without_leaking_the_error_text() -> None:
    recorder = Recorder()
    task = _send(_app(recorder, client=BrokenClient()))

    assert task["status"]["state"] == "TASK_STATE_FAILED"
    span = recorder.span("a2a.task.execute")
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "Lemonade backend is unavailable."
    assert "secret-host" not in recorder.everything_exported()
    assert recorder.total("lemonade_a2a.task.completed", state="failed") == 1


# --- metrics -------------------------------------------------------------------------


def test_metrics_describe_the_request_and_the_task() -> None:
    recorder = Recorder()
    _send(_app(recorder))

    assert recorder.total("http.server.request.duration", **{"http.route": "/message:send"}) == 1
    assert recorder.total("lemonade_a2a.task.completed", state="completed") == 1
    assert recorder.total("lemonade_a2a.task.duration", state="completed") == 1
    assert recorder.total("lemonade_a2a.stream.ttft") == 1
    assert recorder.total("lemonade_a2a.stream.chunk_interval") == 1  # two chunks, one gap
    assert recorder.total("lemonade_a2a.task.active") == 0  # started and finished


def test_refused_requests_are_counted_by_reason() -> None:
    recorder = Recorder()
    client = _app(recorder, api_keys="a:secret-a", rate_limit_per_minute=2, max_request_bytes=500)

    client.get("/tasks", headers=HEADERS)  # no key
    good = {**HEADERS, "Authorization": "Bearer secret-a"}
    client.get("/tasks", headers=good)
    client.get("/tasks", headers=good)
    client.get("/tasks", headers=good)  # over the budget
    client.post("/message:send", headers=good, content=b"x" * 2000)  # too large

    assert recorder.total("lemonade_a2a.task.rejected", reason="auth") == 1
    assert recorder.total("lemonade_a2a.task.rejected", reason="rate_limit") >= 1
    assert recorder.total("lemonade_a2a.task.rejected", reason="size") == 1
    # Refused requests are traced and measured as well.
    assert recorder.total("http.server.request.duration", **{"http.response.status_code": 401}) == 1


def test_backend_failures_are_counted_by_class() -> None:
    recorder = Recorder()
    _send(_app(recorder, client=BrokenClient()))

    # BrokenClient bypasses LemonadeClient, so exercise the real client's classes directly.
    def handler(request):
        raise httpx.ReadTimeout("slow")

    lemonade = LemonadeClient("http://lemonade/v1", "m", telemetry=recorder.telemetry)
    lemonade._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    _send(_app(recorder, client=lemonade), text="second")

    assert recorder.total("lemonade_a2a.backend.errors", **{"class": "timeout"}) == 1
    assert recorder.total("gen_ai.client.operation.duration", **{"error.type": "timeout"}) == 1


def test_token_usage_is_recorded_only_when_lemonade_reports_it() -> None:
    recorder = Recorder()
    body = (
        'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3}}\n\n'
    )
    lemonade = LemonadeClient("http://lemonade/v1", "m", telemetry=recorder.telemetry)
    lemonade._http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    _send(_app(recorder, client=lemonade))

    assert recorder.total("gen_ai.client.token.usage", **{"gen_ai.token.type": "input"}) == 1
    assert recorder.total("gen_ai.client.token.usage", **{"gen_ai.token.type": "output"}) == 1
    quiet = Recorder()
    _send(_app(quiet))
    assert quiet.total("gen_ai.client.token.usage") == 0


def test_metric_cardinality_stays_bounded_over_many_tasks() -> None:
    recorder = Recorder(capture="metadata")  # even with ids on spans, never on metrics
    client = _app(recorder, max_stored_tasks=50)

    for index in range(300):
        _send(client, text=f"prompt number {index}")

    counts = recorder.series_count()
    assert counts, "no metrics recorded"
    assert max(counts.values()) <= 10, counts
    assert recorder.total("lemonade_a2a.store.evictions") > 0


# --- privacy modes -------------------------------------------------------------------


def _ids(recorder: Recorder) -> list[str]:
    return [str(v) for s in recorder.spans for k, v in (s.attributes or {}).items() if "id" in k]


def test_none_mode_records_no_task_or_context_ids() -> None:
    recorder = Recorder(capture="none")
    task = _send(_app(recorder))

    assert task["id"] not in recorder.everything_exported()
    assert "a2a.task.id" not in recorder.span("a2a.task.execute").attributes
    assert "a2a.context.id" not in recorder.span("a2a.task.execute").attributes


def test_metadata_mode_adds_ids_but_never_text() -> None:
    recorder = Recorder(capture="metadata")
    task = _send(_app(recorder), text="a confidential prompt")

    attributes = recorder.span("a2a.task.execute").attributes
    assert attributes["a2a.task.id"] == task["id"]
    assert "a confidential prompt" not in recorder.everything_exported()


def test_content_mode_records_truncated_text_as_events() -> None:
    recorder = Recorder(capture="content", content_max=12)
    _send(_app(recorder), text="a very long confidential prompt indeed")

    events = {e.name: dict(e.attributes) for e in recorder.span("a2a.task.execute").events}
    assert events["lemonade_a2a.prompt"]["content"].startswith("a very long ")
    assert len(events["lemonade_a2a.prompt"]["content"]) <= 13  # limit plus the ellipsis
    assert "lemonade_a2a.response" in events


def test_redaction_patterns_remove_attributes() -> None:
    recorder = Recorder(capture="metadata", redact=("a2a.*.id", "http.route"))
    _send(_app(recorder))

    server = recorder.span("POST /message:send")
    assert "http.route" not in server.attributes
    assert "a2a.task.id" not in recorder.span("a2a.task.execute").attributes
    assert "a2a.input.chars" in recorder.span("a2a.task.execute").attributes


@pytest.mark.parametrize("capture", ["none", "metadata"])
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    secret=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=8, max_size=60)
)
def test_prompts_never_appear_in_exported_telemetry(capture: str, secret: str) -> None:
    """Whatever the prompt says, none/metadata modes export none of it: not in span names,
    attributes, events, status text or metric attributes, on success or failure."""
    marker = f"LEAK<{secret}>"
    recorder = Recorder(capture=capture)
    _send(_app(recorder), text=marker)
    _send(_app(recorder, client=BrokenClient()), text=marker + "-failing")

    assert "LEAK<" not in recorder.everything_exported()


def test_log_lines_carry_trace_ids_and_no_prompt_text(caplog) -> None:
    recorder = Recorder()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    lines: list[str] = []
    handler.emit = lambda record: lines.append(handler.format(record)) or None  # type: ignore[method-assign]
    logger = logging.getLogger("lemonade_a2a")
    logger.addHandler(handler)
    try:
        _send(_app(recorder, client=BrokenClient()), text="a confidential prompt")
    finally:
        logger.removeHandler(handler)

    records = [json.loads(line) for line in lines]
    failed = [r for r in records if r.get("event") == "backend.error"]
    assert failed, records
    assert failed[0]["trace_id"] == format(
        recorder.span("a2a.task.execute").context.trace_id, "032x"
    )
    assert "a confidential prompt" not in "\n".join(lines)


# --- off by default ------------------------------------------------------------------


def test_the_noop_facade_accepts_every_call() -> None:
    off = Telemetry.noop()

    with off.span("x") as span:
        span.set_attribute("a", 1)
    off.task_started()
    off.task_finished("completed", 0.1)
    off.rejected("auth")
    off.first_chunk(0.1)
    off.backend_error("timeout")
    off.event(None, "e")
    off.inject({})
    off.remember_task("t", span.get_span_context())
    assert off.link_for_task("t") == []
    assert not off.enabled and NOOP.enabled is False


def test_apps_without_telemetry_behave_exactly_as_before() -> None:
    client = TestClient(create_app(Settings(), executor=LemonadeAgentExecutor(EchoClient())))

    assert _send(client)["status"]["state"] == "TASK_STATE_COMPLETED"


# --- helpers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "route"),
    [
        ("/", "/"),
        ("/message:send", "/message:send"),
        ("/a2a/rest/message:stream", "/message:stream"),
        ("/tasks", "/tasks"),
        ("/tasks/abc-123", "/tasks/{id}"),
        ("/tasks/abc-123:cancel", "/tasks/{id}:cancel"),
        ("/a2a/rest/tasks/abc:subscribe", "/tasks/{id}:subscribe"),
        ("/tasks/abc/pushNotificationConfigs/9", "/tasks/{id}/{x}/{x}"),
        ("/some-tenant/message:send", "/{tenant}/..."),
        ("/whatever", "other"),
    ],
)
def test_route_templates_hide_ids_and_tenants(path: str, route: str) -> None:
    assert route_template(path) == route


def test_task_ids_are_read_from_rest_paths_only() -> None:
    assert task_id_from_path("/tasks/abc:cancel") == "abc"
    assert task_id_from_path("/a2a/rest/tasks/xyz") == "xyz"
    assert task_id_from_path("/message:send") is None


def test_identity_handle_is_stable_and_not_the_name() -> None:
    telemetry = Recorder().telemetry

    handle = telemetry.identity("alice")

    assert handle == telemetry.identity("alice") != telemetry.identity("bob")
    assert "alice" not in handle
