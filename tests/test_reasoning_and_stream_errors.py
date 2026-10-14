"""Reasoning models and errors that arrive inside a successful stream."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from a2a.types import Message, Part, Role, TaskState

from lemonade_a2a.executor import (
    ARTIFACT_NAME,
    NO_ANSWER_TEXT,
    REASONING_ARTIFACT_NAME,
    LemonadeAgentExecutor,
)
from lemonade_a2a.lemonade_client import (
    BackendStreamError,
    Delta,
    LemonadeClient,
    StreamFormatError,
    _delta_parts,
    describe_stream_error,
    error_class,
)


class Queue:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


def _context(text: str = "hello"):
    message = Message(message_id="m1", role=Role.ROLE_USER, parts=[Part(text=text)])
    return SimpleNamespace(
        message=message,
        task_id="t1",
        context_id="c1",
        get_user_input=lambda: text,
        call_context=SimpleNamespace(state={}),
    )


class Scripted:
    """A client that replays a fixed list of deltas."""

    def __init__(self, *deltas: Delta):
        self.deltas = deltas

    async def stream_events(self, messages):
        for delta in self.deltas:
            yield delta


def _artifacts(queue: Queue):
    return [e for e in queue.events if hasattr(e, "last_chunk") and e.HasField("artifact")]


def _text(artifacts, name: str) -> str:
    return "".join(
        part.text for e in artifacts if e.artifact.name == name for part in e.artifact.parts
    )


def _final(queue: Queue):
    statuses = [e for e in queue.events if getattr(e, "status", None) and e.HasField("status")]
    return statuses[-1].status


THINKING = [Delta("reasoning", "let me "), Delta("reasoning", "think")]
ANSWER = [Delta("content", "the "), Delta("content", "answer")]


async def test_reasoning_is_dropped_by_default() -> None:
    queue = Queue()

    await LemonadeAgentExecutor(Scripted(*THINKING, *ANSWER)).execute(_context(), queue)

    artifacts = _artifacts(queue)
    assert _text(artifacts, ARTIFACT_NAME) == "the answer"
    assert REASONING_ARTIFACT_NAME not in {e.artifact.name for e in artifacts}
    assert _final(queue).state == TaskState.TASK_STATE_COMPLETED


async def test_reasoning_can_be_streamed_as_a_separate_artifact() -> None:
    queue = Queue()

    await LemonadeAgentExecutor(Scripted(*THINKING, *ANSWER), reasoning="artifact").execute(
        _context(), queue
    )

    artifacts = _artifacts(queue)
    assert _text(artifacts, REASONING_ARTIFACT_NAME) == "let me think"
    assert _text(artifacts, ARTIFACT_NAME) == "the answer"  # the answer artifact stays clean
    reasoning = [e for e in artifacts if e.artifact.name == REASONING_ARTIFACT_NAME]
    assert reasoning[-1].last_chunk is True  # closed before the answer starts
    assert len({e.artifact.artifact_id for e in reasoning}) == 1
    assert reasoning[0].artifact.metadata["lemonade_a2a.kind"] == "reasoning"
    first_answer = next(
        i for i, e in enumerate(queue.events) if e in artifacts and e.artifact.name == ARTIFACT_NAME
    )
    last_reasoning = max(i for i, e in enumerate(queue.events) if e in reasoning)
    assert last_reasoning < first_answer
    assert _final(queue).state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.parametrize("mode", ["drop", "artifact"])
async def test_all_reasoning_and_no_answer_fails_the_task(mode: str) -> None:
    queue = Queue()

    await LemonadeAgentExecutor(Scripted(*THINKING), reasoning=mode).execute(_context(), queue)

    status = _final(queue)
    assert status.state == TaskState.TASK_STATE_FAILED
    assert status.message.parts[0].text == NO_ANSWER_TEXT
    reasoning = [e for e in _artifacts(queue) if e.artifact.name == REASONING_ARTIFACT_NAME]
    if mode == "artifact":
        assert reasoning and reasoning[-1].last_chunk is True  # never left open
    else:
        assert not reasoning


async def test_an_empty_response_still_completes() -> None:
    """A model that says nothing at all (no reasoning either) is not a failure."""
    queue = Queue()

    await LemonadeAgentExecutor(Scripted()).execute(_context(), queue)

    assert _final(queue).state == TaskState.TASK_STATE_COMPLETED


def test_unknown_reasoning_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="reasoning"):
        LemonadeAgentExecutor(Scripted(), reasoning="loud")


# --- the real client against recorded streams ------------------------------------------


def _client(body: str) -> LemonadeClient:
    client = LemonadeClient("http://lemonade/v1", "m")
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    return client


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


async def test_client_separates_reasoning_from_the_answer() -> None:
    body = _sse(
        {"choices": [{"delta": {"role": "assistant", "content": None}}]},
        {"choices": [{"delta": {"reasoning_content": "hmm"}}]},
        {"choices": [{"delta": {"content": "42"}}]},
    )
    client = _client(body)

    deltas = [d async for d in client.stream_events([{"role": "user", "content": "x"}])]
    answers = [t async for t in client.stream([{"role": "user", "content": "x"}])]

    assert deltas == [Delta("reasoning", "hmm"), Delta("content", "42")]
    assert answers == ["42"]


CONTEXT_ERROR = {
    "error": {
        "code": 400,
        "message": "request (1647 tokens) exceeds the available context size (1536 tokens)",
        "type": "exceed_context_size_error",
        "n_ctx": 1536,
    }
}


async def test_an_error_event_inside_a_200_stream_fails_the_task() -> None:
    """Seen on real Lemonade: a prompt longer than the model's context arrives as an error
    event in a successful stream. It used to complete the task with an empty answer."""
    queue = Queue()

    await LemonadeAgentExecutor(_client(_sse(CONTEXT_ERROR))).execute(_context(), queue)

    status = _final(queue)
    assert status.state == TaskState.TASK_STATE_FAILED
    assert status.message.parts[0].text == "The request is longer than the model's context window."
    assert "1647" not in status.message.parts[0].text  # backend detail stays in the log


async def test_an_error_after_some_output_closes_the_artifact_first() -> None:
    body = _sse({"choices": [{"delta": {"content": "partial"}}]}, {"error": {"code": 500}})
    queue = Queue()

    await LemonadeAgentExecutor(_client(body)).execute(_context(), queue)

    artifacts = _artifacts(queue)
    assert artifacts[-1].last_chunk is True
    assert _final(queue).state == TaskState.TASK_STATE_FAILED
    assert _final(queue).message.parts[0].text == "Lemonade reported an error while generating."


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (
            {"code": 400, "type": "exceed_context_size_error"},
            "The request is longer than the model's context window.",
        ),
        ({"code": 404, "type": "model_not_found"}, "Lemonade rejected the request (HTTP 404)."),
        ({"code": 503}, "Lemonade reported an error while generating."),
        ({"status_code": 429}, "Lemonade rejected the request (HTTP 429)."),
        ("just a string", "Lemonade reported an error while generating."),
    ],
)
def test_stream_errors_become_client_safe_text(error, text) -> None:
    assert describe_stream_error(BackendStreamError(error)) == text


def test_error_event_shapes() -> None:
    with pytest.raises(BackendStreamError):
        _delta_parts({"error": {"code": 500}})
    with pytest.raises(BackendStreamError):
        _delta_parts({"error": "boom"})
    assert _delta_parts({"error": None, "choices": []}) == ("", "")
    assert issubclass(BackendStreamError, StreamFormatError)  # still a ValueError for callers
    assert error_class(BackendStreamError({})) == "stream_error"
