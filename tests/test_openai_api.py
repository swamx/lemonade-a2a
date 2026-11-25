"""The OpenAI-compatible chat surface and the Lemonade UI registration command."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from lemonade_a2a import builtin, cli
from lemonade_a2a.config import Settings
from lemonade_a2a.executor import LemonadeAgentExecutor, Turn
from lemonade_a2a.lemonade_client import BackendStreamError, Delta
from lemonade_a2a.openai_api import OpenAICompat, RequestError, model_id
from lemonade_a2a.server import create_app


class EchoBackend:
    """Streams thinking, then "seen: <every message's content joined by |>"."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[list[dict]] = []
        self.fail = fail

    async def stream_events(self, messages):
        self.calls.append(messages)
        if self.fail is not None:
            raise self.fail
        yield Delta("reasoning", "thinking ")
        yield Delta("content", "seen: ")
        yield Delta("content", "|".join(str(m["content"]) for m in messages))

    async def aclose(self) -> None:
        pass


@pytest.fixture
def backend(monkeypatch) -> EchoBackend:
    backend = EchoBackend()
    monkeypatch.setattr(builtin, "create_backend", lambda settings, telemetry: backend)
    return backend


def chat(client: TestClient, messages, **extra):
    return client.post("/v1/chat/completions", json={"messages": messages, **extra})


def sse(response) -> tuple[str, str, list]:
    content, reasoning, other = [], [], []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if raw == "[DONE]":
            other.append("DONE")
            continue
        event = json.loads(raw)
        if "error" in event:
            other.append(event["error"])
            continue
        delta = event["choices"][0]["delta"]
        content.append(delta.get("content") or "")
        reasoning.append(delta.get("reasoning_content") or "")
    return "".join(content), "".join(reasoning), other


def test_the_agent_is_listed_as_one_model(backend) -> None:
    with TestClient(create_app(Settings(agent_name="Trip Planner"))) as client:
        listing = client.get("/v1/models").json()
        assert [m["id"] for m in listing["data"]] == ["trip-planner"]
        assert listing["data"][0]["context_length"] == 25_000
        assert client.get("/v1/models/tripplanner.trip-planner").json()["id"] == "trip-planner"
        assert client.get("/v1/models/other").status_code == 404
        assert client.get("/.well-known/agent-card.json").status_code == 200  # A2A untouched


def test_a_completion_uses_the_whole_conversation_and_keeps_thinking_apart(backend) -> None:
    with TestClient(create_app(Settings(agent_name="Trip Planner"))) as client:
        response = chat(
            client,
            [
                {"role": "developer", "content": "Be brief."},
                {"role": "user", "content": "I live in Porto."},
                {"role": "assistant", "content": None, "tool_calls": []},
                {"role": "tool", "content": "ignored"},
                {"role": "user", "content": [{"type": "text", "text": "Where do I live?"}]},
            ],
            model="tripplanner.trip-planner",
            temperature=0.2,
        )
    message = response.json()["choices"][0]["message"]
    assert response.status_code == 200
    assert message["content"] == "seen: Be brief.|I live in Porto.||Where do I live?"
    assert message["reasoning_content"] == "thinking "
    assert [m["role"] for m in backend.calls[0]] == ["system", "user", "assistant", "user"]


def test_streaming_sends_openai_chunks_and_done(backend) -> None:
    with TestClient(create_app(Settings())) as client:
        response = chat(client, [{"role": "user", "content": "hi"}], stream=True)
    content, reasoning, other = sse(response)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert (content, reasoning, other) == ("seen: hi", "thinking ", ["DONE"])


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ([], "JSON object"),
        ({"messages": []}, "1 to"),
        ({"messages": [{"role": "robot", "content": "x"}]}, "role"),
        ({"messages": [{"role": "assistant", "content": "x"}]}, "last message"),
        ({"messages": [{"role": "user", "content": "  "}]}, "last message"),
        ({"messages": [{"role": "user", "content": 5}]}, "string or a list"),
        ({"messages": [{"role": "user", "content": [{"type": "audio"}]}]}, "unsupported"),
        ({"model": "someone-else", "messages": [{"role": "user", "content": "x"}]}, "unknown"),
        ({"messages": [{"role": "user", "content": "x" * 200_001}]}, "character limit"),
    ],
)
def test_bad_requests_get_a_clear_400(backend, body, message) -> None:
    with TestClient(create_app(Settings())) as client:
        response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 400
    assert message in response.json()["error"]["message"]


def test_images_need_the_image_setting_and_inline_data(backend) -> None:
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0K"}}
    remote = {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}
    text = {"type": "text", "text": "What is this?"}
    with TestClient(create_app(Settings())) as client:
        assert chat(client, [{"role": "user", "content": [text, image]}]).status_code == 400
    with TestClient(create_app(Settings(accept_files=True, accept_images=True))) as client:
        assert chat(client, [{"role": "user", "content": [text, image]}]).status_code == 200
        assert chat(client, [{"role": "user", "content": [text, remote]}]).status_code == 400
        # An image earlier in the conversation is not sent again.
        earlier = [{"role": "user", "content": [image]}, {"role": "assistant", "content": "a cat"}]
        response = chat(client, [*earlier, {"role": "user", "content": "And now?"}])
    assert response.json()["choices"][0]["message"]["content"] == "seen: [image]|a cat|And now?"
    assert isinstance(backend.calls[0][-1]["content"], list)  # image_url part for the model


def test_backend_failures_are_sanitized_in_both_modes(monkeypatch) -> None:
    failing = EchoBackend(fail=httpx.ConnectError("refused at 10.0.0.7"))
    monkeypatch.setattr(builtin, "create_backend", lambda settings, telemetry: failing)
    with TestClient(create_app(Settings())) as client:
        plain = chat(client, [{"role": "user", "content": "hi"}])
        streamed = chat(client, [{"role": "user", "content": "hi"}], stream=True)
    assert plain.status_code == 502
    assert plain.json()["error"]["message"] == "Lemonade backend is unavailable."
    _, _, other = sse(streamed)
    assert other[0]["message"] == "Lemonade backend is unavailable." and other[-1] == "DONE"
    assert "10.0.0.7" not in plain.text + streamed.text


def test_failure_texts() -> None:
    assert OpenAICompat._failure_text(TimeoutError())[0] == 504
    assert OpenAICompat._failure_text(BackendStreamError({"type": "exceed_context_size_error"}))[
        1
    ].startswith("The request is longer")
    assert OpenAICompat._failure_text(RuntimeError("x")) == (502, "The agent could not answer.")


async def test_the_concurrency_cap_applies_and_answers_429() -> None:
    from lemonade_a2a.executor import BusyError

    executor = LemonadeAgentExecutor(EchoBackend(), max_concurrent_tasks=1)
    executor._active["running"] = object()  # the only slot is taken
    with pytest.raises(BusyError):
        [delta async for delta in executor.run_turn(Turn(text="hi"))]
    assert OpenAICompat._failure_text(BusyError("full"))[0] == 429
    del executor._active["running"]
    assert [d.text async for d in executor.run_turn(Turn(text="hi"))][-1] == "hi"
    assert executor._active == {}  # the slot is given back


def test_the_surface_can_be_switched_off(backend) -> None:
    with TestClient(create_app(Settings(openai_api=False))) as client:
        assert client.get("/v1/models").status_code == 404


def test_api_keys_protect_the_surface_like_every_route(backend) -> None:
    keys = "lemonade:k1"  # pragma: allowlist secret
    with TestClient(create_app(Settings(api_keys=keys))) as client:
        assert client.get("/v1/models").status_code == 401
        listed = client.get("/v1/models", headers={"Authorization": "Bearer k1"})
        assert listed.status_code == 200


def test_a_conversation_is_found_again_from_its_messages() -> None:
    surface = OpenAICompat(Settings(), LemonadeAgentExecutor(None))
    first, messages, _ = surface.parse({"messages": [{"role": "user", "content": "hi"}]}, "ada")
    surface._remember(
        "ada", [*messages, {"role": "assistant", "content": "hello"}], first.context_id
    )
    follow = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "again"},
    ]
    again, _, _ = surface.parse({"messages": follow}, "ada")
    other_caller, _, _ = surface.parse({"messages": follow}, "bob")
    fresh, _, _ = surface.parse({"messages": [{"role": "user", "content": "hi"}]}, "ada")
    assert again.context_id == first.context_id
    assert other_caller.context_id != first.context_id
    assert fresh.context_id != first.context_id  # a new chat is a new conversation
    surface._max_contexts = 1
    surface._remember("ada", [{"role": "user", "content": "x"}], "c2")
    assert len(surface._contexts) == 1


async def test_a_question_or_returned_files_reach_the_chat() -> None:
    from a2a.types import Part

    class Asks(LemonadeAgentExecutor):
        async def _deltas(self, turn):
            turn.question = "Weather Agent needs more information: Which city?"
            turn.artifacts.append(("delegated-files", [Part(raw=b"x", filename="t.pdf")]))
            return
            yield  # pragma: no cover - makes this an async generator

    surface = OpenAICompat(Settings(), Asks(None))
    out = [item async for item in surface._deltas(Turn(text="q"))]
    assert out[0] == ("content", "Weather Agent needs more information: Which city?")
    assert "t.pdf" in out[1][1]


def test_model_ids() -> None:
    assert model_id("Trip Planner") == "trip-planner"
    assert model_id("!!!") == "agent"


def test_error_types_are_plain_value_errors() -> None:
    assert issubclass(RequestError, ValueError)


# -- `lemonade-a2a lemonade-ui` -----------------------------------------------------------


def test_lemonade_ui_add_and_remove(monkeypatch, capsys) -> None:
    sent: list[tuple[str, dict]] = []

    def post(url, json, timeout, trust_env):
        sent.append((url, json))
        found = {"models_discovered": 1} if url.endswith("/install") else {"status": "success"}
        return httpx.Response(200, json=found, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:13305/v1")
    assert cli.main(["lemonade-ui", "add", "--name", "Trip Planner", "--port", "9100"]) == 0
    assert cli.main(["lemonade-ui", "remove", "--name", "Trip Planner"]) == 0

    (install_url, install), (remove_url, remove) = sent
    assert install_url == "http://127.0.0.1:13305/api/v1/install"
    assert install == {
        "backend": "cloud",
        "provider": "tripplanner",
        "base_url": "http://localhost:9100/v1",
        "api_key": "local",  # pragma: allowlist secret
        "allow_insecure_http": True,
    }
    assert remove_url.endswith("/api/v1/uninstall") and remove["provider"] == "tripplanner"
    assert "pick it in the model list" in capsys.readouterr().out


def test_lemonade_ui_uses_the_agents_own_key_and_reports_problems(monkeypatch, capsys) -> None:
    replies = iter(
        [
            httpx.Response(200, json={"models_discovered": 0}),
            httpx.Response(400, text="bad provider"),
        ]
    )
    sent = []

    def post(url, json, timeout, trust_env):
        sent.append(json)
        response = next(replies)
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setenv("LEMONADE_A2A_API_KEY", "agent-key")  # pragma: allowlist secret
    assert cli.main(["lemonade-ui", "add", "--lemonade", "http://lemonade.lan:13305"]) == 1
    assert sent[0]["api_key"] == "agent-key"  # pragma: allowlist secret
    assert cli.main(["lemonade-ui", "add"]) == 2
    err = capsys.readouterr().err
    assert "found no model" in err and "refused (400)" in err

    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "post", unreachable)
    assert cli.main(["lemonade-ui", "add"]) == 2
    assert "not reachable" in capsys.readouterr().err
    assert cli.main(["lemonade-ui", "add", "--name", "X"]) == 2  # still unreachable
    monkeypatch.delenv("LEMONADE_A2A_API_KEY")
    monkeypatch.setenv("LEMONADE_A2A_OPENAI_API", "0")
    assert cli.main(["lemonade-ui", "add"]) == 2
    assert "OPENAI_API is off" in capsys.readouterr().err


def test_messages_are_normalized_for_strict_chat_templates() -> None:
    """Gemma's template refuses a second system message (HTTP 400 from Lemonade); seen live
    when a caller's system prompt met the agent's own compose instructions."""
    from lemonade_a2a.executor import normalize_messages

    image = [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "d"}}]
    messages = [
        {"role": "system", "content": "agent rules"},
        {"role": "system", "content": "caller rules"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "system", "content": "late rule"},
        {"role": "user", "content": image},
        {"role": "user", "content": "d"},
    ]
    assert normalize_messages(messages) == [
        {"role": "system", "content": "agent rules\n\ncaller rules\n\nlate rule"},
        {"role": "user", "content": "a\n\nb"},
        {"role": "assistant", "content": "c"},
        {"role": "user", "content": image},
        {"role": "user", "content": "d"},
    ]
    assert normalize_messages([{"role": "user", "content": "x"}]) == [
        {"role": "user", "content": "x"}
    ]
    assert messages[2] == {"role": "user", "content": "a"}  # the input is not modified
