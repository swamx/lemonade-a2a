from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from a2a.types import Message, Part, Role

from lemonade_a2a.executor import LemonadeAgentExecutor


class BlockingClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream(self, messages):
        assert messages == [{"role": "user", "content": "hello"}]
        try:
            self.started.set()
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_executor_tracks_and_cancels_active_coroutine() -> None:
    client = BlockingClient()
    executor = LemonadeAgentExecutor(client)  # type: ignore[arg-type]
    context = SimpleNamespace(
        message=Message(
            message_id="m1",
            role=Role.ROLE_USER,
            parts=[Part(text="hello")],
        ),
        task_id="task-1",
        context_id="context-1",
        get_user_input=lambda: "hello",
    )

    async def enqueue_event(event) -> None:
        pass

    queue = SimpleNamespace(enqueue_event=enqueue_event)
    task = asyncio.create_task(executor.execute(context, queue))
    await asyncio.wait_for(client.started.wait(), timeout=2)
    await executor.cancel(context, queue)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert client.closed.is_set()
    assert "task-1" not in executor._active
