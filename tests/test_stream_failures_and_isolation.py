"""Mid-stream failures leave clean artifacts; simultaneous tasks never mix their output."""

from __future__ import annotations

import asyncio
import json
import random
from types import SimpleNamespace

import httpx
import pytest
from a2a.types import Message, Part, Role, TaskState
from fastapi import FastAPI

from lemonade_a2a.config import Settings
from lemonade_a2a.executor import ARTIFACT_NAME, LemonadeAgentExecutor
from lemonade_a2a.lemonade_client import LemonadeClient
from lemonade_a2a.server import create_app

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


class Queue:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


def _context(text="hello", task_id="t1"):
    message = Message(message_id="m1", role=Role.ROLE_USER, parts=[Part(text=text)])
    return SimpleNamespace(
        message=message,
        task_id=task_id,
        context_id="c1",
        get_user_input=lambda: text,
        call_context=SimpleNamespace(state={}),
    )


class FailsAfter:
    """Streams two chunks, then fails the way the backend or the network would."""

    def __init__(self, error):
        self.error = error

    async def stream(self, messages):
        yield "partial "
        yield "output"
        raise self.error


def _final_status(events):
    statuses = [e for e in events if getattr(e, "status", None) and e.HasField("status")]
    return statuses[-1].status


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (httpx.ReadError("connection reset"), "unavailable"),
        (httpx.ReadTimeout("slow"), "in time"),
        (json.JSONDecodeError("bad", "{", 0), "unreadable"),
    ],
)
async def test_a_stream_that_fails_midway_closes_its_artifact(error, needle) -> None:
    queue = Queue()

    await LemonadeAgentExecutor(FailsAfter(error)).execute(_context(), queue)

    artifacts = [e for e in queue.events if getattr(e, "artifact", None) and e.artifact.parts]
    assert artifacts, "expected artifact events"
    chunks = [e for e in queue.events if hasattr(e, "last_chunk") and e.HasField("artifact")]
    assert chunks[-1].last_chunk is True  # closed, not left open
    assert len({e.artifact.artifact_id for e in chunks}) == 1
    assert chunks[-1].artifact.name == ARTIFACT_NAME
    status = _final_status(queue.events)
    assert status.state == TaskState.TASK_STATE_FAILED
    assert needle in status.message.parts[0].text
    # The terminal status comes after the artifact is closed.
    assert queue.events.index(chunks[-1]) < queue.events.index(
        next(
            e for e in queue.events if getattr(e, "status", None) and e.status.state == status.state
        )
    )


async def test_deadline_mid_stream_also_closes_the_artifact() -> None:
    class Endless:
        async def stream(self, messages):
            yield "tick "
            await asyncio.sleep(10)

    queue = Queue()

    await LemonadeAgentExecutor(Endless(), max_task_seconds=0.1).execute(_context(), queue)

    chunks = [e for e in queue.events if hasattr(e, "last_chunk") and e.HasField("artifact")]
    assert chunks[-1].last_chunk is True
    assert _final_status(queue.events).state == TaskState.TASK_STATE_FAILED


async def test_failure_before_any_output_adds_no_empty_artifact() -> None:
    class Down:
        async def stream(self, messages):
            raise httpx.ConnectError("refused")
            yield  # pragma: no cover

    queue = Queue()

    await LemonadeAgentExecutor(Down()).execute(_context(), queue)

    assert not [e for e in queue.events if hasattr(e, "last_chunk") and e.HasField("artifact")]
    assert _final_status(queue.events).state == TaskState.TASK_STATE_FAILED


async def test_unparseable_backend_stream_is_a_failed_task() -> None:
    """End to end through LemonadeClient: a garbled SSE line becomes FAILED, not a crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        lines = 'data: {"choices":[{"delta":{"content":"ok "}}]}\n\ndata: {not json\n\n'
        return httpx.Response(200, content=lines, headers={"content-type": "text/event-stream"})

    client = LemonadeClient("http://lemonade/v1", "m")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    queue = Queue()

    await LemonadeAgentExecutor(client).execute(_context(), queue)

    status = _final_status(queue.events)
    assert status.state == TaskState.TASK_STATE_FAILED
    assert "unreadable" in status.message.parts[0].text


# --- simultaneous tasks stay separate ----------------------------------------------


class Jittery:
    """Echoes each prompt back in small chunks with random pauses, so tasks interleave."""

    async def stream(self, messages):
        prompt = messages[0]["content"]
        for index in range(0, len(prompt), 3):
            await asyncio.sleep(random.random() * 0.01)
            yield prompt[index : index + 3]


def _app() -> FastAPI:
    executor = LemonadeAgentExecutor(Jittery(), max_concurrent_tasks=64)
    return create_app(Settings(), executor=executor)


async def test_simultaneous_tasks_never_mix_their_output() -> None:
    prompts = [f"task-{index:03d}:" + chr(97 + index % 26) * (20 + index) for index in range(40)]
    transport = httpx.ASGITransport(app=_app())

    async with httpx.AsyncClient(transport=transport, base_url="http://adapter") as http:

        async def run(prompt: str):
            body = {
                "message": {
                    "messageId": prompt,
                    "role": "ROLE_USER",
                    "parts": [{"text": prompt}],
                }
            }
            response = await http.post("/message:send", headers=HEADERS, content=json.dumps(body))
            task = response.json()["task"]
            text = "".join(
                part.get("text", "") for artifact in task["artifacts"] for part in artifact["parts"]
            )
            return prompt, task, text

        results = await asyncio.gather(*(run(p) for p in prompts))

    assert len({task["id"] for _, task, _ in results}) == len(prompts)
    for prompt, task, text in results:
        assert text == prompt, f"{task['id']} got another task's output"
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert [h["parts"][0]["text"] for h in task["history"]] == [prompt]


async def test_executor_bookkeeping_is_empty_after_a_burst() -> None:
    executor = LemonadeAgentExecutor(Jittery(), max_concurrent_tasks=64)
    queues = [Queue() for _ in range(30)]

    await asyncio.gather(
        *(
            executor.execute(_context(f"prompt {i}", task_id=f"t{i}"), queue)
            for i, queue in enumerate(queues)
        )
    )

    assert executor._active == {}
