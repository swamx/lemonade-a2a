"""Fuzzing: malformed requests and malformed backend output must never crash the adapter.

Property-based (hypothesis). The server must answer every request with a well-formed
response (never a 5xx, never an unhandled exception) and keep serving afterwards; the
Lemonade stream parser may only fail with ``ValueError`` or an httpx error.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lemonade_a2a.config import Settings
from lemonade_a2a.executor import LemonadeAgentExecutor
from lemonade_a2a.lemonade_client import LemonadeClient
from lemonade_a2a.server import create_app

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
FUZZ = settings(
    max_examples=120,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


class EchoClient:
    async def stream(self, messages):
        yield "ok"


@pytest.fixture(scope="module")
def client():
    app = create_app(
        Settings(max_request_bytes=65_536), executor=LemonadeAgentExecutor(EchoClient())
    )
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


def _assert_alive(client: TestClient) -> None:
    """After any abuse a normal request must still complete."""
    body = {"message": {"messageId": "alive", "role": "ROLE_USER", "parts": [{"text": "hi"}]}}
    response = client.post("/message:send", headers=HEADERS, content=json.dumps(body))
    assert response.status_code == 200
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


text = st.text(max_size=200)  # includes NUL, surrogates-free unicode, control characters
scalars = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | text
json_values = st.recursive(
    scalars,
    lambda children: st.lists(children, max_size=5) | st.dictionaries(text, children, max_size=5),
    max_leaves=25,
)

part = st.fixed_dictionaries(
    {},
    optional={
        "text": json_values,
        "raw": json_values,
        "url": json_values,
        "data": json_values,
        "mediaType": json_values,
        "filename": json_values,
        "metadata": json_values,
    },
)
message = st.fixed_dictionaries(
    {},
    optional={
        "messageId": json_values,
        "role": json_values,
        "contextId": json_values,
        "taskId": json_values,
        "parts": st.lists(part, max_size=5) | json_values,
        "metadata": json_values,
        "extensions": json_values,
        "referenceTaskIds": json_values,
    },
)
send_params = st.fixed_dictionaries(
    {},
    optional={
        "message": message | json_values,
        "configuration": json_values,
        "metadata": json_values,
        "tenant": json_values,
    },
)


def _check(response: httpx.Response) -> None:
    assert response.status_code < 500, response.text[:300]
    if response.headers.get("content-type", "").startswith(("application/json", "application/a2a")):
        assert isinstance(response.json(), dict | list)


@FUZZ
@given(params=send_params, path=st.sampled_from(["/message:send", "/message:stream"]))
def test_malformed_rest_messages_never_crash(client, params, path) -> None:
    _check(client.post(path, headers=HEADERS, content=json.dumps(params)))


@FUZZ
@given(
    method=st.sampled_from(
        ["SendMessage", "SendStreamingMessage", "GetTask", "ListTasks", "CancelTask", "x", ""]
    )
    | text,
    params=send_params | json_values,
    request_id=json_values,
)
def test_malformed_jsonrpc_never_crashes(client, method, params, request_id) -> None:
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    response = client.post("/", headers=HEADERS, content=json.dumps(body))
    _check(response)
    if response.status_code == 200 and response.headers["content-type"].startswith(
        "application/json"
    ):
        reply = response.json()
        assert reply.get("jsonrpc") == "2.0" and ("result" in reply or "error" in reply)


@FUZZ
@given(
    raw=st.binary(max_size=2000),
    content_type=st.sampled_from(["application/json", "text/plain", ""]),
)
def test_arbitrary_bytes_never_crash(client, raw, content_type) -> None:
    headers = {"A2A-Version": "1.0", **({"Content-Type": content_type} if content_type else {})}
    for path in ("/", "/message:send", "/tasks/x:cancel"):
        _check(client.post(path, headers=headers, content=raw))


@FUZZ
@given(
    task_id=text,
    query=st.dictionaries(
        st.sampled_from(["pageSize", "pageToken", "state", "contextId", "historyLength"]),
        text,
        max_size=4,
    ),
)
def test_malformed_task_queries_never_crash(client, task_id, query) -> None:
    _check(client.get(f"/tasks/{quote(task_id, safe='')}", headers=HEADERS, params=query))
    _check(client.get("/tasks", headers=HEADERS, params=query))


def test_adapter_still_serves_after_fuzzing(client) -> None:
    _assert_alive(client)


def test_oversized_and_deeply_nested_json_is_rejected_cleanly(client) -> None:
    deep = "[" * 5000 + "]" * 5000
    for body in (deep, '{"message":' * 3000, "9" * 5000):
        _check(client.post("/message:send", headers=HEADERS, content=body))
    _check(client.post("/message:send", headers=HEADERS, content=b"x" * 100_000))
    _assert_alive(client)


# --- the Lemonade stream parser ------------------------------------------------------

sse_line = st.one_of(
    st.just("data: [DONE]"),
    st.just(""),
    text,
    text.map(lambda value: "data:" + value),
    json_values.map(lambda value: "data: " + json.dumps(value)),
    st.builds(
        lambda choices: "data: " + json.dumps({"choices": choices}),
        json_values,
    ),
)


@FUZZ
@given(lines=st.lists(sse_line, max_size=12))
async def test_stream_parser_only_fails_with_value_or_http_errors(lines) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = "\n".join(lines).encode("utf-8", "replace")
        return httpx.Response(200, content=content)

    lemonade = LemonadeClient("http://lemonade/v1", "m")
    lemonade._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        async for chunk in lemonade.stream([{"role": "user", "content": "x"}]):
            assert isinstance(chunk, str) and chunk
    except (ValueError, httpx.HTTPError):
        pass
    finally:
        await lemonade.aclose()
