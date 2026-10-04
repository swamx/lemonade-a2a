from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Message, Part, Task, TaskState, TaskStatus
from a2a.utils.errors import ContentTypeNotSupportedError, InvalidParamsError

from .lemonade_client import LemonadeClient

LOG = logging.getLogger("lemonade_a2a.executor")

ARTIFACT_NAME = "lemonade-response"
DEFAULT_MAX_INPUT_CHARS = 100_000


def describe_backend_error(exc: httpx.HTTPError) -> str:
    """Client-safe failure text; raw exception details stay in the server log."""
    if isinstance(exc, httpx.TimeoutException):
        return "Lemonade did not respond in time."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Lemonade rejected the request (HTTP {exc.response.status_code})."
    return "Lemonade backend is unavailable."


class LemonadeAgentExecutor(AgentExecutor):
    """Execute A2A text tasks using a Lemonade OpenAI-compatible endpoint.

    Active inference coroutines are tracked by A2A task id so a protocol-level
    cancel request can stop the local streaming request instead of only changing
    task metadata. Backend failures end the task in ``TASK_STATE_FAILED`` rather
    than surfacing as a protocol-level internal error.
    """

    def __init__(
        self, client: LemonadeClient, *, max_input_chars: int = DEFAULT_MAX_INPUT_CHARS
    ) -> None:
        self.client = client
        self.max_input_chars = max_input_chars
        self._active: dict[str, asyncio.Task[None]] = {}
        self._active_lock = asyncio.Lock()

    def _validated_query(self, context: RequestContext, message: Message) -> str:
        if any(part.WhichOneof("content") != "text" for part in message.parts):
            raise ContentTypeNotSupportedError()

        query = context.get_user_input()
        if not query.strip():
            raise InvalidParamsError("A2A request did not contain non-empty text input")
        if len(query) > self.max_input_chars:
            raise InvalidParamsError(f"Input exceeds the {self.max_input_chars} character limit")
        return query

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        task_id = context.task_id
        context_id = context.context_id
        if message is None or not task_id or not context_id:
            raise InvalidParamsError("A2A request is missing message/task/context identifiers")

        current = asyncio.current_task()
        if current is not None:
            async with self._active_lock:
                self._active[task_id] = current

        try:
            query = self._validated_query(context, message)

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

            try:
                await self._stream_artifact(updater, query)
            except httpx.HTTPError as exc:
                LOG.warning("Lemonade request failed for task %s: %r", task_id, exc)
                await updater.failed(
                    updater.new_agent_message([Part(text=describe_backend_error(exc))])
                )
                return
            await updater.complete()
        finally:
            async with self._active_lock:
                if self._active.get(task_id) is current:
                    self._active.pop(task_id, None)

    async def _stream_artifact(self, updater: TaskUpdater, query: str) -> None:
        artifact_id = uuid.uuid4().hex
        emitted = False
        async for text in self.client.stream([{"role": "user", "content": query}]):
            await updater.add_artifact(
                parts=[Part(text=text)],
                artifact_id=artifact_id,
                name=ARTIFACT_NAME,
                append=emitted,
                last_chunk=False,
            )
            emitted = True

        await updater.add_artifact(
            parts=[Part(text="")],
            artifact_id=artifact_id,
            name=ARTIFACT_NAME,
            append=emitted,
            last_chunk=True,
        )

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
