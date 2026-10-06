"""A persistent task store on SQLite (``LEMONADE_A2A_TASK_STORE=sqlite``).

Built on the A2A SDK's ``DatabaseTaskStore`` (SQLAlchemy); this module adds what an
adapter needs on top of it:

* **Restart recovery.** A task that was still running when the process stopped has no
  executor any more. It is reported ``FAILED`` ("the adapter restarted") the first time it is
  read, instead of staying ``WORKING`` forever.
* **A bound.** The oldest *finished* tasks are deleted past ``max_tasks``; running tasks
  never are.

Requires the ``sqlite`` extra: ``pip install 'lemonade-a2a[sqlite]'``. It is also the
reference implementation of the ``lemonade_a2a.task_stores`` extension point.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types import Message, Part, Role, TaskState
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task

from .task_store import TERMINAL_STATES
from .telemetry import NOOP, Telemetry

LOG = logging.getLogger("lemonade_a2a.stores")

RESTART_TEXT = "The adapter restarted while this task was running."
_TERMINAL_NAMES = tuple(TaskState.Name(state) for state in TERMINAL_STATES)


class SqliteTaskStore(TaskStore):
    def __init__(self, path: str | Path, max_tasks: int, telemetry: Telemetry = NOOP) -> None:
        if max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        try:
            from a2a.server.tasks.database_task_store import DatabaseTaskStore
            from sqlalchemy.ext.asyncio import create_async_engine
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise RuntimeError(
                "the sqlite task store needs the 'sqlite' extra: pip install 'lemonade-a2a[sqlite]'"
            ) from exc
        self.path = Path(path)
        self._create_private_file()
        self.max_tasks = max_tasks
        self.telemetry = telemetry
        self._engine = create_async_engine(f"sqlite+aiosqlite:///{self.path.as_posix()}")
        self._inner = DatabaseTaskStore(self._engine)
        self._live: set[str] = set()  # tasks this process is running (non-terminal)
        self._size = 0
        telemetry.bind_store(lambda: self._size)

    def _create_private_file(self) -> None:
        """Task text (prompts and answers) is stored here: create the file owner-only (0600)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))

    # ----- TaskStore ----------------------------------------------------------------

    async def save(self, task: Task, context: ServerCallContext) -> None:
        if task.status.state in TERMINAL_STATES:
            self._live.discard(task.id)
        else:
            self._live.add(task.id)
        await self._inner.save(task, context)
        await self._trim()

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        task = await self._inner.get(task_id, context)
        return await self._recovered(task, context) if task is not None else None

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        response = await self._inner.list(params, context)
        for index, task in enumerate(response.tasks):
            fixed = await self._recovered(task, context)
            if fixed is not task:
                response.tasks[index].CopyFrom(fixed)
        return response

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        self._live.discard(task_id)
        await self._inner.delete(task_id, context)

    async def aclose(self) -> None:
        await self._engine.dispose()

    # ----- internals ----------------------------------------------------------------

    async def _recovered(self, task: Task, context: ServerCallContext) -> Task:
        """Report an orphaned running task as failed, once, and remember it."""
        if task.status.state in TERMINAL_STATES or task.id in self._live:
            return task
        LOG.warning(
            "Task %s was running when the adapter stopped; marking it failed",
            task.id,
            extra={"event": "task.recovered"},
        )
        task.status.state = TaskState.TASK_STATE_FAILED
        task.status.message.CopyFrom(
            Message(
                message_id=uuid.uuid4().hex,
                role=Role.ROLE_AGENT,
                parts=[Part(text=RESTART_TEXT)],
            )
        )
        await self._inner.save(task, context)
        return task

    async def _trim(self) -> None:
        from sqlalchemy import text

        terminal = ", ".join(f"'{name}'" for name in _TERMINAL_NAMES)
        async with self._engine.begin() as connection:
            count = (await connection.execute(text("SELECT COUNT(*) FROM tasks"))).scalar_one()
            excess = count - self.max_tasks
            if excess > 0:
                result = await connection.execute(
                    text(
                        "DELETE FROM tasks WHERE id IN (SELECT id FROM tasks WHERE "
                        f"json_extract(status, '$.state') IN ({terminal}) "
                        "ORDER BY rowid ASC LIMIT :excess)"
                    ),
                    {"excess": excess},
                )
                for _ in range(result.rowcount or 0):
                    self.telemetry.eviction()
                count -= result.rowcount or 0
        self._size = count
