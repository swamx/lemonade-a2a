from __future__ import annotations

from collections import OrderedDict

from a2a.server.context import ServerCallContext
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import TaskState
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task

TERMINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)


class BoundedTaskStore(TaskStore):
    """In-memory task store that evicts the oldest *finished* tasks past ``max_tasks``.

    Running and waiting tasks are never evicted: their number is already bounded
    by the executor's concurrency limit, and dropping them would lose live state.
    """

    def __init__(self, max_tasks: int, inner: TaskStore | None = None) -> None:
        if max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        self.max_tasks = max_tasks
        self._inner = inner or InMemoryTaskStore()
        # task id -> (call context used to save it, is terminal); oldest first.
        self._known: OrderedDict[str, tuple[ServerCallContext, bool]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._known)

    async def save(self, task: Task, context: ServerCallContext) -> None:
        await self._inner.save(task, context)
        self._known[task.id] = (context, task.status.state in TERMINAL_STATES)
        self._known.move_to_end(task.id)
        await self._evict()

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        return await self._inner.get(task_id, context)

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        return await self._inner.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._inner.delete(task_id, context)
        self._known.pop(task_id, None)

    async def _evict(self) -> None:
        while len(self._known) > self.max_tasks:
            victim = next(
                (task_id for task_id, (_, terminal) in self._known.items() if terminal), None
            )
            if victim is None:  # only live tasks remain; the concurrency cap bounds them
                return
            context, _ = self._known.pop(victim)
            await self._inner.delete(victim, context)
