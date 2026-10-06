"""Extension API v1: swap the backend, task store, authenticator or telemetry wiring.

Third-party packages register a *factory* under an entry-point group; the adapter picks one
by name from its settings (``LEMONADE_A2A_BACKEND``, ``_TASK_STORE``, ``_AUTHENTICATOR``,
``_TELEMETRY_PLUGIN``). A factory carries an ``api_version`` attribute; a mismatch is reported
by ``lemonade-a2a doctor`` and refused at startup. Built-in implementations use the same
seams, so they double as reference plugins. See docs/specification.md §8 and docs/extending.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from a2a.server.tasks.task_store import TaskStore

    from .config import Settings
    from .lemonade_client import Delta
    from .telemetry import Telemetry

API_VERSION = 1

GROUPS = {
    "backends": "lemonade_a2a.backends",
    "task_stores": "lemonade_a2a.task_stores",
    "authenticators": "lemonade_a2a.authenticators",
    "telemetry": "lemonade_a2a.telemetry",
}


class PluginError(RuntimeError):
    """A requested plugin is missing, broken or built for another extension API version."""


# ----- the contracts ---------------------------------------------------------------------


@runtime_checkable
class Backend(Protocol):
    """Where answers come from. The built-in one talks to Lemonade's OpenAI-compatible API.

    ``stream_events`` yields :class:`~lemonade_a2a.lemonade_client.Delta` objects, answer
    text as ``kind="content"`` and optional thinking as ``kind="reasoning"``. Raise
    ``httpx.HTTPError`` for transport failures and ``ValueError`` (or a subclass) for output
    that cannot be understood; the executor maps both to a failed task with a safe message.
    """

    def stream_events(self, messages: list[dict[str, Any]]) -> AsyncIterator[Delta]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class Authenticator(Protocol):
    """Decides who is calling. Return the caller's identity (the task owner) or ``None``."""

    async def authenticate(self, headers: Mapping[str, str]) -> str | None: ...


BackendFactory = Callable[["Settings", "Telemetry"], Backend]
TaskStoreFactory = Callable[["Settings", "Telemetry"], "TaskStore"]
AuthenticatorFactory = Callable[["Settings"], Authenticator]
TelemetryFactory = Callable[["Settings"], "Telemetry"]


# ----- discovery ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginInfo:
    group: str
    name: str
    target: str
    distribution: str
    version: str
    api_version: int | None
    status: str  # ok | api_mismatch | load_error
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _entry_points(group: str) -> list[metadata.EntryPoint]:
    return list(metadata.entry_points(group=group))


def discover(group: str) -> list[PluginInfo]:
    """Installed third-party plugins in one group, each loaded once to read its api_version."""
    found = []
    for entry in _entry_points(group):
        distribution = getattr(entry, "dist", None)
        name = distribution.metadata["Name"] if distribution else "?"
        version = distribution.version if distribution else "?"
        try:
            factory = entry.load()
            api = getattr(factory, "api_version", None)
            status = "ok" if api == API_VERSION else "api_mismatch"
            detail = (
                "" if status == "ok" else f"built for extension API {api}, this is {API_VERSION}"
            )
        except Exception as exc:  # noqa: BLE001 - a broken plugin must be reported, not crash discovery
            api, status, detail = None, "load_error", f"{type(exc).__name__}: {exc}"
        found.append(PluginInfo(group, entry.name, entry.value, name, version, api, status, detail))
    return found


def discover_all() -> list[PluginInfo]:
    return [info for group in GROUPS.values() for info in discover(group)]


def load(group: str, name: str, builtin: Mapping[str, Callable[..., Any]]) -> Callable[..., Any]:
    """The factory called ``name``: a built-in first, then an installed entry point."""
    if name in builtin:
        return builtin[name]
    for entry in _entry_points(group):
        if entry.name != name:
            continue
        try:
            factory = entry.load()
        except Exception as exc:
            raise PluginError(f"plugin {name!r} ({group}) failed to load: {exc}") from exc
        api = getattr(factory, "api_version", None)
        if api != API_VERSION:
            raise PluginError(
                f"plugin {name!r} ({group}) targets extension API {api}; this adapter provides "
                f"{API_VERSION}"
            )
        return factory
    installed = sorted({*builtin, *(e.name for e in _entry_points(group))})
    raise PluginError(f"no {group} plugin named {name!r}; available: {', '.join(installed)}")


def plugin(factory: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for plugin authors: stamps the factory with the API version it was written for."""
    factory.api_version = API_VERSION  # type: ignore[attr-defined]
    return factory
