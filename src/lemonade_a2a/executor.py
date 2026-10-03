from __future__ import annotations

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

from .lemonade_client import LemonadeClient


class LemonadeAgentExecutor(AgentExecutor):
    """Execute A2A text tasks using a Lemonade OpenAI-compatible endpoint."""

    def __init__(self, client: LemonadeClient) -> None:
        self.client = client

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        task_id = context.task_id
        context_id = context.context_id
        if message is None or not task_id or not context_id:
            raise ValueError("A2A request is missing message/task/context identifiers")

        query = context.get_user_input()
        if not query.strip():
            raise ValueError("A2A request did not contain non-empty text input")

        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[message],
            )
        )
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )
        await updater.start_work()

        emitted = False
        async for text in self.client.stream([{"role": "user", "content": query}]):
            emitted = True
            await updater.add_artifact(
                parts=[Part(text=text)],
                name="lemonade-response",
                append=True,
                last_chunk=False,
            )

        if not emitted:
            # Some backends/configurations may not provide streaming deltas.
            answer = await self.client.chat([{"role": "user", "content": query}])
            await updater.add_artifact(
                parts=[Part(text=answer)],
                name="lemonade-response",
                append=False,
                last_chunk=True,
            )
        else:
            # Close the streamed artifact without inventing additional model text.
            await updater.add_artifact(
                parts=[Part(text="")],
                name="lemonade-response",
                append=True,
                last_chunk=True,
            )

        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=context.task_id or "",
            context_id=context.context_id or "",
        )
        await updater.cancel()
