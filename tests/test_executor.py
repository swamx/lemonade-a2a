from types import SimpleNamespace

import pytest

from lemonade_a2a.executor import LemonadeAgentExecutor


class FakeClient:
    async def chat(self, messages):
        assert messages == [{"role": "user", "content": "hello"}]
        return "hello from Lemonade"


class FakeQueue:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_executor_emits_task_and_completion_events() -> None:
    message = SimpleNamespace(message_id="m1")
    context = SimpleNamespace(
        message=message,
        task_id="t1",
        context_id="c1",
        get_user_input=lambda: "hello",
    )
    queue = FakeQueue()

    await LemonadeAgentExecutor(FakeClient()).execute(context, queue)

    assert len(queue.events) >= 3
    assert queue.events[0].id == "t1"
