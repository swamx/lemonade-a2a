import pytest
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState, TaskStatus

from lemonade_a2a.task_store import BoundedTaskStore

COMPLETED = TaskState.TASK_STATE_COMPLETED
WORKING = TaskState.TASK_STATE_WORKING


def _task(task_id: str, state: TaskState) -> Task:
    return Task(id=task_id, context_id="c", status=TaskStatus(state=state))


@pytest.mark.asyncio
async def test_oldest_finished_tasks_are_evicted_first() -> None:
    store, ctx = BoundedTaskStore(max_tasks=2), ServerCallContext()
    for task_id in ("a", "b", "c"):
        await store.save(_task(task_id, COMPLETED), ctx)

    assert len(store) == 2
    assert await store.get("a", ctx) is None
    assert await store.get("c", ctx) is not None


@pytest.mark.asyncio
async def test_live_tasks_are_never_evicted() -> None:
    store, ctx = BoundedTaskStore(max_tasks=1), ServerCallContext()
    await store.save(_task("live", WORKING), ctx)
    await store.save(_task("done", COMPLETED), ctx)
    await store.save(_task("live2", WORKING), ctx)

    assert await store.get("live", ctx) is not None
    assert await store.get("live2", ctx) is not None
    assert await store.get("done", ctx) is None


@pytest.mark.asyncio
async def test_updating_a_task_refreshes_its_age() -> None:
    store, ctx = BoundedTaskStore(max_tasks=2), ServerCallContext()
    await store.save(_task("a", COMPLETED), ctx)
    await store.save(_task("b", COMPLETED), ctx)
    await store.save(_task("a", COMPLETED), ctx)  # a is now the newest
    await store.save(_task("c", COMPLETED), ctx)

    assert await store.get("b", ctx) is None
    assert await store.get("a", ctx) is not None


@pytest.mark.asyncio
async def test_delete_forgets_the_task() -> None:
    store, ctx = BoundedTaskStore(max_tasks=5), ServerCallContext()
    await store.save(_task("a", COMPLETED), ctx)
    await store.delete("a", ctx)

    assert len(store) == 0
    assert await store.get("a", ctx) is None


def test_max_tasks_must_be_positive() -> None:
    with pytest.raises(ValueError):
        BoundedTaskStore(max_tasks=0)
