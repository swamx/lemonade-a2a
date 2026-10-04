"""A2A TCK system under test built on the real Lemonade A2A server layer.

The official TCK drives behavior through ``messageId`` prefixes defined in its
Gherkin scenarios (file artifacts, input-required, rejection, ...). A thin LLM
adapter does not implement those, so this SUT swaps only the *executor* for a
scenario executor while reusing everything else from ``lemonade_a2a.server``:
routes, Agent Card, version negotiation, error mapping and content types.

What a TCK run therefore certifies is the protocol surface. Lemonade inference
is covered separately (mock E2E and real-Lemonade validation).

Usage:  python -m tck.tck_sut   (env: TCK_SUT_PORT, default 9999)
"""

from __future__ import annotations

import asyncio
import os

import uvicorn
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Part
from a2a.utils.errors import A2AError
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Value

from lemonade_a2a.config import Settings
from lemonade_a2a.server import create_app

STREAMING_TIMEOUT_S = float(os.getenv("TCK_STREAMING_TIMEOUT", "2.0"))
FILE_PART = {"raw": b"tck", "media_type": "text/plain", "filename": "output.txt"}


class TckScenarioExecutor(AgentExecutor):
    """Implements the TCK scenarios (scenarios/*.feature) by messageId prefix."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id, context_id, message = context.task_id, context.context_id, context.message
        if not task_id or not context_id or message is None:
            return
        prefix = message.message_id
        updater = TaskUpdater(event_queue, task_id, context_id)
        if prefix.startswith("tck-message-response"):
            # A direct Message reply must not be preceded by a Task event.
            await event_queue.enqueue_event(
                updater.new_agent_message([Part(text="Direct message response")])
            )
            return
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(message))
        else:
            # Follow-up messages continue the stored task, whatever contextId they carried.
            updater = TaskUpdater(event_queue, task_id, context.current_task.context_id)

        async def artifact(*parts: Part, **kwargs) -> None:
            await updater.add_artifact(parts=list(parts), **kwargs)

        if prefix.startswith("tck-stream-artifact-chunked"):
            await updater.start_work()
            await artifact(Part(text="chunk-1 "), append=True)
            await artifact(Part(text="chunk-2"), append=True, last_chunk=True)
            await updater.complete()
        elif prefix.startswith("test-resubscribe-message-id"):
            await updater.start_work()
            await asyncio.sleep(2 * STREAMING_TIMEOUT_S)
            await updater.complete()
        elif prefix.startswith("tck-stream-artifact-file"):
            await updater.start_work()
            await artifact(Part(**FILE_PART))
            await updater.complete()
        elif prefix.startswith("tck-stream-artifact-text"):
            await updater.start_work()
            await artifact(Part(text="Streamed text content"))
            await updater.complete()
        elif prefix.startswith("tck-stream-ordering-001"):
            await updater.start_work()
            await artifact(Part(text="Ordered output"))
            await updater.complete()
        elif prefix.startswith("tck-stream-001"):
            await updater.start_work()
            await artifact(Part(text="Stream hello from TCK"))
            await updater.complete()
        elif prefix.startswith("tck-stream-002"):
            await updater.complete()
        elif prefix.startswith("tck-stream-003"):
            await updater.start_work()
            await artifact(Part(text="Stream task lifecycle"))
            await updater.complete()
        elif prefix.startswith("tck-artifact-file-url"):
            await artifact(
                Part(
                    url="https://example.com/output.txt",
                    media_type="text/plain",
                    filename="output.txt",
                )
            )
            await updater.complete()
        elif prefix.startswith("tck-artifact-file"):
            await artifact(Part(**FILE_PART))
            await updater.complete()
        elif prefix.startswith("tck-artifact-text"):
            await artifact(Part(text="Generated text content"))
            await updater.complete()
        elif prefix.startswith("tck-artifact-data"):
            data = json_format.Parse('{"key": "value", "count": 42}', Value())
            await artifact(Part(data=data))
            await updater.complete()
        elif prefix.startswith("tck-input-required"):
            await updater.requires_input()
        elif prefix.startswith("tck-reject-task"):
            raise A2AError("rejected")
        elif prefix.startswith("tck-complete-task"):
            await updater.complete(updater.new_agent_message([Part(text="Hello from TCK")]))
        else:
            await updater.complete(
                updater.new_agent_message([Part(text=f"Unhandled messageId prefix: {prefix}")])
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id and context.context_id:
            await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def build_app():
    port = int(os.getenv("TCK_SUT_PORT", "9999"))
    settings = Settings(port=port, public_url=f"http://127.0.0.1:{port}")
    return create_app(settings, executor=TckScenarioExecutor())


if __name__ == "__main__":
    uvicorn.run(build_app(), host="127.0.0.1", port=int(os.getenv("TCK_SUT_PORT", "9999")))
