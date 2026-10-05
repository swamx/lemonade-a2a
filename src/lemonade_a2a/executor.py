from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

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
DEFAULT_MAX_INPUT_PARTS = 32
DEFAULT_MAX_TASK_SECONDS = 600.0
DEFAULT_MAX_CONCURRENT_TASKS = 8


def describe_backend_error(exc: httpx.HTTPError) -> str:
    """Client-safe failure text; raw exception details stay in the server log."""
    if isinstance(exc, httpx.TimeoutException):
        return "Lemonade did not respond in time."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Lemonade rejected the request (HTTP {exc.response.status_code})."
    return "Lemonade backend is unavailable."


@dataclass
class _Progress:
    """What a task has streamed so far, so a failure can close the artifact cleanly."""

    artifact_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    emitted: bool = False
    closed: bool = False


async def _close_partial_artifact(updater: TaskUpdater, progress: _Progress) -> None:
    if progress.emitted and not progress.closed:
        await updater.add_artifact(
            parts=[Part(text="")],
            artifact_id=progress.artifact_id,
            name=ARTIFACT_NAME,
            append=True,
            last_chunk=True,
        )
        progress.closed = True


class LemonadeAgentExecutor(AgentExecutor):
    """Execute A2A text tasks using a Lemonade OpenAI-compatible endpoint.

    Active inference coroutines are tracked by A2A task id so a protocol-level
    cancel request can stop the local streaming request instead of only changing
    task metadata. Backend failures end the task in ``TASK_STATE_FAILED`` rather
    than surfacing as a protocol-level internal error.

    Resource bounds: input size/parts, a wall-clock deadline per task and a cap
    on concurrently running tasks (excess tasks are ``REJECTED``). A client that
    disconnects from a stream does not cancel its task (A2A tasks outlive
    connections and can be resubscribed); the deadline bounds abandoned work and
    ``CancelTask`` stops it explicitly. ``cancel_on_disconnect`` opts into the
    opposite policy for the streaming request that started a task: when that
    client goes away the task is cancelled like an explicit ``CancelTask``.

    A stream that fails part-way (backend error or deadline) closes its artifact
    with ``last_chunk`` before the task becomes ``FAILED``, so clients never see
    an artifact that is left open.
    """

    def __init__(
        self,
        client: LemonadeClient,
        *,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_input_parts: int = DEFAULT_MAX_INPUT_PARTS,
        max_task_seconds: float = DEFAULT_MAX_TASK_SECONDS,
        max_concurrent_tasks: int = DEFAULT_MAX_CONCURRENT_TASKS,
        cancel_on_disconnect: bool = False,
    ) -> None:
        self.client = client
        self.max_input_chars = max_input_chars
        self.max_input_parts = max_input_parts
        self.max_task_seconds = max_task_seconds
        self.max_concurrent_tasks = max_concurrent_tasks
        self.cancel_on_disconnect = cancel_on_disconnect
        self._active: dict[str, asyncio.Task[None]] = {}
        self._active_lock = asyncio.Lock()

    def _validated_query(self, context: RequestContext, message: Message) -> str:
        if len(message.parts) > self.max_input_parts:
            raise InvalidParamsError(f"Input exceeds {self.max_input_parts} parts")
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

        query = self._validated_query(context, message)

        current = asyncio.current_task()
        async with self._active_lock:
            busy = len(self._active) >= self.max_concurrent_tasks
            if current is not None and not busy:
                self._active[task_id] = current

        try:
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
            if busy:
                await updater.reject(
                    updater.new_agent_message(
                        [Part(text="Too many concurrent tasks; retry later.")]
                    )
                )
                return
            await updater.start_work()

            watcher, fired = self._watch_disconnect(context, event_queue)
            progress = _Progress()
            try:
                async with asyncio.timeout(self.max_task_seconds):
                    await self._stream_artifact(updater, query, progress)
            except TimeoutError:
                LOG.warning("Task %s exceeded %.0fs deadline", task_id, self.max_task_seconds)
                await self._fail(updater, progress, "Task exceeded its time limit.")
                return
            except httpx.HTTPError as exc:
                LOG.warning("Lemonade request failed for task %s: %r", task_id, exc)
                await self._fail(updater, progress, describe_backend_error(exc))
                return
            except ValueError as exc:  # unparseable stream from the backend
                LOG.warning("Lemonade sent an unreadable response for task %s: %r", task_id, exc)
                await self._fail(updater, progress, "Lemonade returned an unreadable response.")
                return
            finally:
                if watcher is not None:
                    if fired.is_set():  # let it finish publishing the CANCELED status
                        await asyncio.gather(watcher, return_exceptions=True)
                    else:
                        watcher.cancel()
            await updater.complete()
        finally:
            async with self._active_lock:
                if self._active.get(task_id) is current:
                    self._active.pop(task_id, None)

    @staticmethod
    async def _fail(updater: TaskUpdater, progress: _Progress, text: str) -> None:
        await _close_partial_artifact(updater, progress)
        await updater.failed(updater.new_agent_message([Part(text=text)]))

    def _watch_disconnect(
        self, context: RequestContext, event_queue: EventQueue
    ) -> tuple[asyncio.Task[None] | None, asyncio.Event]:
        """Cancel this task when the request that started it disconnects (opt-in).

        Returns the watcher (None when the mode is off or the request has no
        disconnect signal) and an event that is set once the watcher has fired.
        """
        fired = asyncio.Event()
        if not self.cancel_on_disconnect:
            return None, fired
        disconnected = context.call_context.state.get("disconnected")
        if disconnected is None:
            return None, fired

        async def watch() -> None:
            await disconnected.wait()
            fired.set()
            LOG.info("Client of task %s disconnected; cancelling it", context.task_id)
            await self.cancel(context, event_queue)

        return asyncio.create_task(watch()), fired

    async def _stream_artifact(
        self, updater: TaskUpdater, query: str, progress: _Progress | None = None
    ) -> None:
        progress = progress or _Progress()
        async for text in self.client.stream([{"role": "user", "content": query}]):
            await updater.add_artifact(
                parts=[Part(text=text)],
                artifact_id=progress.artifact_id,
                name=ARTIFACT_NAME,
                append=progress.emitted,
                last_chunk=False,
            )
            progress.emitted = True

        await updater.add_artifact(
            parts=[Part(text="")],
            artifact_id=progress.artifact_id,
            name=ARTIFACT_NAME,
            append=progress.emitted,
            last_chunk=True,
        )
        progress.closed = True

    async def shutdown(self) -> None:
        """Cancel in-flight inference and wait for it to unwind (server shutdown)."""
        async with self._active_lock:
            running = [task for task in self._active.values() if not task.done()]
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

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
