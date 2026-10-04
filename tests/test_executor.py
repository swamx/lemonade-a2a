import asyncio
from types import SimpleNamespace

import httpx
import pytest
from a2a.types import Message, Part, Role, TaskState
from a2a.utils.errors import ContentTypeNotSupportedError, InvalidParamsError

from lemonade_a2a.executor import LemonadeAgentExecutor


class FakeClient:
    async def stream(self, messages):
        assert messages == [{"role": "user", "content": "hello"}]
        yield "hello "
        yield "from Lemonade"


class FakeQueue:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_executor_emits_task_and_completion_events() -> None:
    message = Message(
        message_id="m1",
        role=Role.ROLE_USER,
        parts=[Part(text="hello")],
    )
    context = SimpleNamespace(
        message=message,
        task_id="t1",
        context_id="c1",
        get_user_input=lambda: "hello",
    )
    queue = FakeQueue()

    await LemonadeAgentExecutor(FakeClient()).execute(context, queue)

    artifact_events = [event for event in queue.events if getattr(event, "artifact", None)]

    assert len(queue.events) >= 3
    assert queue.events[0].id == "t1"
    assert len(artifact_events) == 3
    assert len({event.artifact.artifact_id for event in artifact_events}) == 1
    assert [event.append for event in artifact_events] == [False, True, True]
    assert artifact_events[-1].last_chunk is True


@pytest.mark.asyncio
async def test_executor_rejects_non_text_parts() -> None:
    context = SimpleNamespace(
        message=Message(
            message_id="m1",
            role=Role.ROLE_USER,
            parts=[Part(raw=b"tck", media_type="application/x-unsupported-tck-type")],
        ),
        task_id="t1",
        context_id="c1",
        get_user_input=lambda: "",
    )

    with pytest.raises(ContentTypeNotSupportedError):
        await LemonadeAgentExecutor(FakeClient()).execute(context, FakeQueue())


def _context(text: str = "hello", task_id: str = "t1"):
    return SimpleNamespace(
        message=Message(message_id="m1", role=Role.ROLE_USER, parts=[Part(text=text)]),
        task_id=task_id,
        context_id="c1",
        get_user_input=lambda: text,
    )


def _final_state(queue: FakeQueue):
    statuses = [e.status.state for e in queue.events if hasattr(e, "status")]
    return statuses[-1]


class FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def stream(self, messages):
        raise self.exc
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("refused"), "unavailable"),
        (httpx.ReadTimeout("slow"), "in time"),
        (
            httpx.HTTPStatusError(
                "bad", request=httpx.Request("POST", "http://x"), response=httpx.Response(404)
            ),
            "HTTP 404",
        ),
    ],
)
async def test_backend_errors_fail_the_task_without_leaking_details(exc, expected) -> None:
    queue = FakeQueue()

    await LemonadeAgentExecutor(FailingClient(exc)).execute(_context(), queue)

    assert _final_state(queue) == TaskState.TASK_STATE_FAILED
    text = "".join(
        part.text
        for e in queue.events
        if hasattr(e, "status") and e.status.HasField("message")
        for part in e.status.message.parts
    )
    assert expected in text
    assert "refused" not in text and "slow" not in text


@pytest.mark.asyncio
async def test_empty_and_oversized_input_are_invalid_params() -> None:
    executor = LemonadeAgentExecutor(FakeClient(), max_input_chars=5)

    with pytest.raises(InvalidParamsError):
        await executor.execute(_context("   "), FakeQueue())
    with pytest.raises(InvalidParamsError):
        await executor.execute(_context("too long"), FakeQueue())


class BlockingClient:
    """Streams one chunk, then waits until released."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, messages):
        yield "partial"
        self.started.set()
        await self.release.wait()
        yield "done"


@pytest.mark.asyncio
async def test_excess_concurrent_tasks_are_rejected() -> None:
    client = BlockingClient()
    executor = LemonadeAgentExecutor(client, max_concurrent_tasks=1)
    first_queue, second_queue = FakeQueue(), FakeQueue()

    first = asyncio.create_task(executor.execute(_context(task_id="a"), first_queue))
    await client.started.wait()
    await executor.execute(_context(task_id="b"), second_queue)

    assert _final_state(second_queue) == TaskState.TASK_STATE_REJECTED
    client.release.set()
    await first
    assert _final_state(first_queue) == TaskState.TASK_STATE_COMPLETED

    # Capacity is released once the first task finishes.
    third_queue = FakeQueue()
    await executor.execute(_context(task_id="c"), third_queue)
    assert _final_state(third_queue) == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_task_deadline_fails_the_task() -> None:
    queue = FakeQueue()
    executor = LemonadeAgentExecutor(BlockingClient(), max_task_seconds=0.05)

    await executor.execute(_context(), queue)

    assert _final_state(queue) == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_shutdown_cancels_running_tasks() -> None:
    client = BlockingClient()
    executor = LemonadeAgentExecutor(client)
    running = asyncio.create_task(executor.execute(_context(), FakeQueue()))
    await client.started.wait()

    await executor.shutdown()

    assert running.cancelled()


@pytest.mark.asyncio
async def test_too_many_parts_are_invalid_params() -> None:
    context = _context()
    context.message = Message(
        message_id="m1", role=Role.ROLE_USER, parts=[Part(text="x") for _ in range(3)]
    )

    with pytest.raises(InvalidParamsError):
        await LemonadeAgentExecutor(FakeClient(), max_input_parts=2).execute(context, FakeQueue())
