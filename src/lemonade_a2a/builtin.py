"""The built-in implementations behind each extension point, and how a setting picks one.

They are ordinary plugins (``@plugin`` factories), so a third-party package that registers
the same kind of factory under an entry-point group is a drop-in replacement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import plugins
from .lemonade_client import LemonadeClient
from .plugins import plugin
from .task_store import BoundedTaskStore
from .telemetry import Telemetry, setup_telemetry

if TYPE_CHECKING:
    from a2a.server.tasks.task_store import TaskStore

    from .config import Settings


@plugin
def lemonade_backend(settings: Settings, telemetry: Telemetry) -> LemonadeClient:
    return LemonadeClient(
        settings.lemonade_base_url,
        settings.model,
        timeout=settings.request_timeout_seconds,
        api_key=settings.lemonade_api_key,
        telemetry=telemetry,
    )


@plugin
def memory_store(settings: Settings, telemetry: Telemetry) -> TaskStore:
    return BoundedTaskStore(settings.max_stored_tasks, telemetry=telemetry)


@plugin
def sqlite_store(settings: Settings, telemetry: Telemetry) -> TaskStore:
    from .stores import SqliteTaskStore

    return SqliteTaskStore(settings.task_db, settings.max_stored_tasks, telemetry)


@plugin
def builtin_telemetry(settings: Settings) -> Telemetry:
    return setup_telemetry(settings)


BACKENDS = {"lemonade": lemonade_backend}
TASK_STORES = {"memory": memory_store, "sqlite": sqlite_store}
AUTHENTICATORS: dict = {}  # API-key auth is built into the middleware; plugins add others
TELEMETRY = {"builtin": builtin_telemetry}


def create_backend(settings: Settings, telemetry: Telemetry):
    return plugins.load(plugins.GROUPS["backends"], settings.backend, BACKENDS)(settings, telemetry)


def create_task_store(settings: Settings, telemetry: Telemetry) -> TaskStore:
    factory = plugins.load(plugins.GROUPS["task_stores"], settings.task_store, TASK_STORES)
    return factory(settings, telemetry)


def create_authenticator(settings: Settings):
    if not settings.authenticator:
        return None
    factory = plugins.load(plugins.GROUPS["authenticators"], settings.authenticator, AUTHENTICATORS)
    return factory(settings)


def create_telemetry(settings: Settings) -> Telemetry:
    name = settings.telemetry_plugin or "builtin"
    return plugins.load(plugins.GROUPS["telemetry"], name, TELEMETRY)(settings)
