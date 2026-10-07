"""Extension API v1 (backends, task stores, authenticators, telemetry) and the SQLite task store."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from a2a.auth.user import UnauthenticatedUser, User
from a2a.server.context import ServerCallContext
from a2a.types import Part, Task, TaskState, TaskStatus
from a2a.types.a2a_pb2 import ListTasksRequest
from fastapi.testclient import TestClient
from starlette.authentication import SimpleUser

from lemonade_a2a import builtin, plugins
from lemonade_a2a.config import Settings
from lemonade_a2a.lemonade_client import Delta
from lemonade_a2a.plugins import PluginError, plugin
from lemonade_a2a.server import create_app
from lemonade_a2a.stores import RESTART_TEXT, SqliteTaskStore
from lemonade_a2a.task_store import BoundedTaskStore
from tests.plugin_helpers import FakeEntryPoint
from tests.telemetry_helpers import Recorder

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


def _send(
    client: TestClient, headers=None, text: str = "hi", message_id: str | None = None
) -> dict:
    body = {
        "message": {
            "messageId": message_id or f"m-{text}",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
    }
    response = client.post("/message:send", headers=headers or HEADERS, content=json.dumps(body))
    assert response.status_code == 200, response.text
    return response.json()["task"]


# --- discovery and loading ------------------------------------------------------------------


def test_built_in_implementations_load_without_any_entry_point() -> None:
    settings = Settings()

    assert (
        builtin.create_backend(settings, Recorder().telemetry).__class__.__name__
        == "LemonadeClient"
    )
    assert isinstance(builtin.create_task_store(settings, Recorder().telemetry), BoundedTaskStore)
    assert builtin.create_authenticator(settings) is None
    assert builtin.create_telemetry(settings).enabled is False


def test_an_installed_plugin_is_found_and_used(installed) -> None:
    @plugin
    def factory(settings, telemetry):
        return "my-backend"

    installed("backends", FakeEntryPoint("mine", factory))

    assert builtin.create_backend(Settings(backend="mine"), Recorder().telemetry) == "my-backend"


def test_unknown_plugins_list_what_is_available(installed) -> None:
    installed("backends", FakeEntryPoint("mine", plugin(lambda s, t: None)))

    with pytest.raises(PluginError, match=r"available: lemonade, mine"):
        builtin.create_backend(Settings(backend="ghost"), Recorder().telemetry)


def test_a_plugin_built_for_another_api_version_is_refused(installed) -> None:
    def old(settings, telemetry):
        return None

    old.api_version = 0  # type: ignore[attr-defined]
    installed("backends", FakeEntryPoint("old", old))
    installed("task_stores", FakeEntryPoint("unstamped", lambda s, t: None))

    with pytest.raises(PluginError, match="extension API 0"):
        builtin.create_backend(Settings(backend="old"), Recorder().telemetry)
    with pytest.raises(PluginError, match="extension API None"):
        builtin.create_task_store(Settings(task_store="unstamped"), Recorder().telemetry)


def test_a_plugin_that_fails_to_import_is_reported(installed) -> None:
    installed("backends", FakeEntryPoint("broken", None, fail=True))

    with pytest.raises(PluginError, match="failed to load"):
        builtin.create_backend(Settings(backend="broken"), Recorder().telemetry)


def test_discovery_reports_every_plugin_with_its_status(installed) -> None:
    installed("backends", FakeEntryPoint("good", plugin(lambda s, t: None)))
    installed("backends", FakeEntryPoint("broken", None, fail=True))
    stale = lambda s, t: None
    stale.api_version = 99
    installed("task_stores", FakeEntryPoint("stale", stale, package="other-plugin"))

    found = {(info.group, info.name): info for info in plugins.discover_all()}

    assert found[("lemonade_a2a.backends", "good")].status == "ok"
    assert found[("lemonade_a2a.backends", "good")].distribution == "acme-plugin"
    assert found[("lemonade_a2a.backends", "broken")].status == "load_error"
    assert "acme_plugin is broken" in found[("lemonade_a2a.backends", "broken")].detail
    stale_info = found[("lemonade_a2a.task_stores", "stale")]
    assert stale_info.status == "api_mismatch" and stale_info.api_version == 99


# --- a backend plugin end to end -----------------------------------------------------------------


class ShoutingBackend:
    """A second backend: answers by shouting the prompt back, then thinks out loud."""

    async def stream_events(self, messages) -> AsyncIterator[Delta]:
        yield Delta("reasoning", "hmm")
        yield Delta("content", messages[0]["content"].upper())

    async def aclose(self) -> None:
        self.closed = True


def test_a_backend_plugin_serves_real_requests(installed) -> None:
    backend = ShoutingBackend()
    installed("backends", FakeEntryPoint("shout", plugin(lambda s, t: backend)))

    with TestClient(create_app(Settings(backend="shout"))) as client:
        task = _send(client, text="make some noise")

    answer = "".join(p.get("text", "") for a in task["artifacts"] for p in a["parts"])
    assert task["status"]["state"] == "TASK_STATE_COMPLETED" and answer == "MAKE SOME NOISE"
    assert getattr(backend, "closed", False)  # the adapter closed it at shutdown


# --- an authenticator plugin --------------------------------------------------------------------------


class HeaderAuthenticator:
    async def authenticate(self, headers):
        return headers.get("x-user") or None


class ExplodingAuthenticator:
    async def authenticate(self, headers):
        raise RuntimeError("identity provider is down")


def _auth_app(installed, authenticator) -> TestClient:
    installed("authenticators", FakeEntryPoint("sso", plugin(lambda settings: authenticator)))
    return TestClient(create_app(Settings(authenticator="sso")))


def test_an_authenticator_plugin_decides_who_is_calling(installed) -> None:
    client = _auth_app(installed, HeaderAuthenticator())

    assert client.get("/tasks", headers=HEADERS).status_code == 401
    assert client.get("/tasks", headers={**HEADERS, "x-user": "alice"}).status_code == 200
    assert client.get("/healthz").status_code == 200  # discovery stays public
    assert client.get("/.well-known/agent-card.json").status_code == 200


def test_the_plugins_identity_is_the_task_owner(installed) -> None:
    client = _auth_app(installed, HeaderAuthenticator())
    alice, bob = {**HEADERS, "x-user": "alice"}, {**HEADERS, "x-user": "bob"}
    installed("backends", FakeEntryPoint("shout", plugin(lambda s, t: ShoutingBackend())))
    client = TestClient(create_app(Settings(authenticator="sso", backend="shout")))
    task = _send(client, headers=alice)

    assert client.get(f"/tasks/{task['id']}", headers=alice).status_code == 200
    assert client.get(f"/tasks/{task['id']}", headers=bob).status_code == 404


def test_a_failing_authenticator_denies_instead_of_letting_requests_through(installed) -> None:
    client = _auth_app(installed, ExplodingAuthenticator())

    assert client.get("/tasks", headers={**HEADERS, "x-user": "alice"}).status_code == 401


# --- a telemetry plugin ----------------------------------------------------------------------------------


def test_a_telemetry_plugin_replaces_the_wiring(installed) -> None:
    recorder = Recorder()
    installed("telemetry", FakeEntryPoint("mine", plugin(lambda settings: recorder.telemetry)))
    installed("backends", FakeEntryPoint("shout", plugin(lambda s, t: ShoutingBackend())))

    with TestClient(create_app(Settings(telemetry_plugin="mine", backend="shout"))) as client:
        _send(client)

    assert recorder.span("POST /message:send")


# --- the SQLite task store -------------------------------------------------------------------------------------


def _task(task_id: str, state: TaskState, context_id: str = "c") -> Task:
    return Task(id=task_id, context_id=context_id, status=TaskStatus(state=state))


class _Named(User):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._name


def _context(owner: str = "") -> ServerCallContext:
    return ServerCallContext(user=_Named(owner) if owner else UnauthenticatedUser())


async def test_tasks_survive_a_restart(tmp_path) -> None:
    path = tmp_path / "tasks.sqlite"
    first = SqliteTaskStore(path, max_tasks=10)
    await first.save(_task("done", TaskState.TASK_STATE_COMPLETED), _context())
    await first.aclose()

    second = SqliteTaskStore(path, max_tasks=10)
    found = await second.get("done", _context())
    await second.aclose()

    assert found is not None and found.status.state == TaskState.TASK_STATE_COMPLETED


async def test_a_task_that_was_running_at_shutdown_is_reported_failed(tmp_path) -> None:
    path = tmp_path / "tasks.sqlite"
    first = SqliteTaskStore(path, max_tasks=10)
    await first.save(_task("orphan", TaskState.TASK_STATE_WORKING), _context())
    # The process "dies" here: no executor will ever finish this task.
    await first.aclose()

    second = SqliteTaskStore(path, max_tasks=10)
    recovered = await second.get("orphan", _context())
    again = await second.get("orphan", _context())
    listed = await second.list(ListTasksRequest(), _context())
    await second.aclose()

    assert recovered is not None and recovered.status.state == TaskState.TASK_STATE_FAILED
    assert recovered.status.message.parts[0].text == RESTART_TEXT
    assert (
        again is not None and again.status.state == TaskState.TASK_STATE_FAILED
    )  # and it stays so
    assert [t.status.state for t in listed.tasks] == [TaskState.TASK_STATE_FAILED]


async def test_a_task_running_in_this_process_is_left_alone(tmp_path) -> None:
    store = SqliteTaskStore(tmp_path / "tasks.sqlite", max_tasks=10)
    await store.save(_task("live", TaskState.TASK_STATE_WORKING), _context())

    found = await store.get("live", _context())
    await store.aclose()

    assert found is not None and found.status.state == TaskState.TASK_STATE_WORKING


async def test_the_oldest_finished_tasks_are_evicted_and_running_ones_never(tmp_path) -> None:
    recorder = Recorder()
    store = SqliteTaskStore(tmp_path / "tasks.sqlite", max_tasks=3, telemetry=recorder.telemetry)
    await store.save(_task("running", TaskState.TASK_STATE_WORKING), _context())
    for index in range(6):
        await store.save(_task(f"done-{index}", TaskState.TASK_STATE_COMPLETED), _context())

    listed = await store.list(ListTasksRequest(), _context())
    ids = {t.id for t in listed.tasks}
    await store.aclose()

    assert len(ids) == 3
    assert "running" in ids  # never evicted
    assert ids - {"running"} == {"done-4", "done-5"}  # the newest finished tasks stay
    assert recorder.total("lemonade_a2a.store.evictions") == 4


async def test_owners_stay_separate_after_persistence(tmp_path) -> None:
    store = SqliteTaskStore(tmp_path / "tasks.sqlite", max_tasks=10)
    await store.save(_task("t", TaskState.TASK_STATE_COMPLETED), _context("alice"))

    assert await store.get("t", _context("alice")) is not None
    assert await store.get("t", _context("bob")) is None
    await store.aclose()


async def test_delete_forgets_the_task(tmp_path) -> None:
    store = SqliteTaskStore(tmp_path / "tasks.sqlite", max_tasks=10)
    await store.save(_task("t", TaskState.TASK_STATE_COMPLETED), _context())
    await store.delete("t", _context())

    assert await store.get("t", _context()) is None
    await store.aclose()


def test_the_bound_must_be_positive(tmp_path) -> None:
    with pytest.raises(ValueError, match="positive"):
        SqliteTaskStore(tmp_path / "x.sqlite", max_tasks=0)


def test_the_sqlite_store_serves_real_requests_across_app_restarts(tmp_path, installed) -> None:
    db = str(tmp_path / "app.sqlite")
    installed("backends", FakeEntryPoint("shout", plugin(lambda s, t: ShoutingBackend())))
    settings = Settings(task_store="sqlite", task_db=db, backend="shout")

    with TestClient(create_app(settings)) as first:
        task_id = _send(first, text="remember me")["id"]

    with TestClient(create_app(settings)) as second:  # a new process, the same file
        again = second.get(f"/tasks/{task_id}", headers=HEADERS)

    assert again.status_code == 200
    assert again.json()["status"]["state"] == "TASK_STATE_COMPLETED"
    artifact = again.json()["artifacts"][0]["parts"]
    assert "".join(p.get("text", "") for p in artifact) == "REMEMBER ME"


def test_the_store_can_be_chosen_from_the_environment(tmp_path) -> None:
    settings = Settings.from_env(
        {"LEMONADE_A2A_TASK_STORE": "SQLite", "LEMONADE_A2A_TASK_DB": str(tmp_path / "e.sqlite")}
    )

    assert settings.task_store == "sqlite"
    assert isinstance(builtin.create_task_store(settings, Recorder().telemetry), SqliteTaskStore)


def test_part_is_importable_for_plugin_authors() -> None:
    """Plugin authors build messages with the SDK's own types; keep that import stable."""
    assert Part(text="x").text == "x" and SimpleUser("a").display_name == "a"


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX file modes")
def test_the_task_database_is_created_owner_only(tmp_path) -> None:
    store = SqliteTaskStore(tmp_path / "private.sqlite", max_tasks=5)

    assert (tmp_path / "private.sqlite").stat().st_mode & 0o777 == 0o600
    assert store.path.exists()
