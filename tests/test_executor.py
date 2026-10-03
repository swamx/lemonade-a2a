from types import SimpleNamespace

import pytest
from a2a.types import Message, Part, Role

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
