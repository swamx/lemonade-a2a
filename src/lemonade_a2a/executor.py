from __future__ import annotations

import asyncio
import uuid

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus
from a2a.utils.errors import ContentTypeNotSupportedError

from .lemonade_client import LemonadeClient


class LemonadeAgentExecutor(AgentExecutor):
    """Execute A2A text tasks using a Lemonade OpenAI-compatible endpoint.

    Active inference coroutines are tracked by A2A task id so a protocol-level
    cancel request can stop the local streaming request instead of only changing
    task metadata.
    """

    def __init__(self, client: LemonadeClient) -> None:
        self.client = client
        self._active: dict[str, asyncio.Task[None]] = {}
        self._active_lock = asyncio.Lock()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        task_id = context.task_id
        context_id = context.context_id
        if message is None or not task_id or not context_id:
            raise ValueError("A2A request is missing message/task/context identifiers")

        current = asyncio.current_task()
        if current is not None:
            async with self._active_lock:
                self._active[task_id] = current

        try:
            if any(part.WhichOneof("content") != "text" for part in message.parts):
                raise ContentTypeNotSupportedError()

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

            artifact_id = uuid.uuid4().hex
            emitted = False
            async for text in self.client.stream([{"role": "user", "content": query}]):
                await updater.add_artifact(
                    parts=[Part(text=text)],
                    artifact_id=artifact_id,
                    name="lemonade-response",
                    append=emitted,
                    last_chunk=False,
                )
                emitted = True

            await updater.add_artifact(
                parts=[Part(text="")],
                artifact_id=artifact_id,
                name="lemonade-response",
                append=emitted,
                last_chunk=True,
            )
            await updater.complete()
        finally:
            async with self._active_lock:
                if self._active.get(task_id) is current:
                    self._active.pop(task_id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context.context_id or "",
        )

        async with self._active_lock:
            running = self._active.get(task_id)
        if running is not None and not running.done():
            running.cancel()

        await updater.cancel()
