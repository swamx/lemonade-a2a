from __future__ import annotations

import asyncio

import pytest

from lemonade_a2a.executor import LemonadeAgentExecutor


class BlockingClient:
    started: asyncio.Event
    closed: asyncio.Event

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream(self, messages):
        self.started.set()
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_executor_tracks_and_cancels_active_coroutine() -> None:
    client = BlockingClient()
    executor = LemonadeAgentExecutor(client)  # type: ignore[arg-type]

    async def inference() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            client.closed.set()

    task = asyncio.create_task(inference())
    async with executor._active_lock:
        executor._active["task-1"] = task

    async with executor._active_lock:
        running = executor._active["task-1"]
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.closed.is_set()
