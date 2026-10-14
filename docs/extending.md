# Extending lemonade-a2a

Four parts of the adapter can be replaced without forking it. Each is an **extension point**: a
small contract (a `typing.Protocol`), a Python entry-point group, and a setting that selects the
implementation by name. The built-in implementations use the same seams, so they are working
examples. Extension API version: **1** (`lemonade_a2a.plugins.API_VERSION`).

| Extension point | Entry-point group | Setting | Replaces | Built-in |
|---|---|---|---|---|
| Backend | `lemonade_a2a.backends` | `LEMONADE_A2A_BACKEND` | Where answers come from | `lemonade` (Lemonade's OpenAI-compatible API) |
| Task store | `lemonade_a2a.task_stores` | `LEMONADE_A2A_TASK_STORE` | Where tasks are kept | `memory` (bounded), `sqlite` (persistent) |
| Authenticator | `lemonade_a2a.authenticators` | `LEMONADE_A2A_AUTHENTICATOR` | Who is calling | none (API keys are built in) |
| Telemetry | `lemonade_a2a.telemetry` | `LEMONADE_A2A_TELEMETRY_PLUGIN` | OpenTelemetry wiring | `builtin` |

## The contract

A plugin is a **factory**: a callable that returns the object, decorated with `@plugin` so it
carries the extension API version it was written for.

```python
# acme_a2a/backend.py
from collections.abc import AsyncIterator

from lemonade_a2a.lemonade_client import Delta
from lemonade_a2a.plugins import plugin


class ShoutingBackend:
    """Answers by shouting the prompt back."""

    async def stream_events(self, messages: list[dict]) -> AsyncIterator[Delta]:
        yield Delta("reasoning", "thinking...")  # optional; kind="reasoning"
        yield Delta("content", messages[0]["content"].upper())

    async def aclose(self) -> None:
        pass


@plugin
def shouting_backend(settings, telemetry):
    return ShoutingBackend()
```

```toml
# pyproject.toml of the plugin package
[project.entry-points."lemonade_a2a.backends"]
shout = "acme_a2a.backend:shouting_backend"
```

```bash
pip install acme-a2a
LEMONADE_A2A_BACKEND=shout lemonade-a2a
lemonade-a2a doctor          # lists the plugin, its package version and API compatibility
```

### Backend

`stream_events(messages)` yields `Delta` objects in order: `Delta("content", text)` for the answer,
`Delta("reasoning", text)` for a reasoning model's thinking (dropped by default; see
`LEMONADE_A2A_REASONING`). Raise `httpx.HTTPError` for transport problems and `ValueError` (or
`lemonade_a2a.lemonade_client.StreamFormatError`) for output you cannot interpret: the executor turns
both into a `FAILED` task with a safe message. Raising `BackendStreamError` reports an error the server
sent inside a successful stream. `aclose()` is called at shutdown. A backend that only has
`stream(messages) -> AsyncIterator[str]` also works (answer text only).

### Task store

A factory `(settings, telemetry) -> a2a.server.tasks.TaskStore`. Optionally give the store
`async aclose()` (called at shutdown). Owner scoping is the store's job: the SDK passes the caller's
identity in the call context, and a store must never return one owner's task to another. The
reference implementation is [`stores.py`](../src/lemonade_a2a/stores.py): SQLite on the SDK's
`DatabaseTaskStore`, with restart recovery and a retention bound.

### Authenticator

A factory `(settings) -> Authenticator` where `Authenticator.authenticate(headers)` returns the
caller's identity (a string; it becomes the task owner) or `None` for "not authenticated". The adapter
answers 401 for `None`, **and also when `authenticate` raises**, so a broken identity provider denies
instead of letting requests through. Discovery (`/.well-known/agent-card.json`) and `/healthz` stay
public. An authenticator counts as authentication for the `lan` and `external` profiles. The Agent
Card does not declare a security scheme for a plugin (the adapter cannot know what it speaks); add one
through your own card customization if clients should discover it.

### Telemetry

A factory `(settings) -> lemonade_a2a.telemetry.Telemetry`. Build a `Telemetry(tracer=..., meter=...)`
around your own providers. See [observability.md](observability.md).

## Versioning and failure modes

* A plugin built for another extension API version is **refused at startup** with a message naming both
  versions, and `lemonade-a2a doctor` reports it. The API follows semantic versioning of its own: a
  change that breaks existing plugins increments `API_VERSION` and keeps the previous version working
  for at least one minor release of the adapter (see [upgrading.md](upgrading.md)).
* A plugin that fails to import is reported by `doctor` as `load_error` and refused at startup if it
  is the one selected; the others are unaffected.
* An unknown plugin name lists what is available.

## Declaring features

If your plugin adds user-visible capability, describe it so that `capabilities` and `doctor` can
reason about it. Plugins ship their own registry entries in the same format as the
[feature registry](features.md) (`state`, `config`, `verified_by` evidence); merging third-party
entries into `capabilities` output is planned and not implemented yet (see the roadmap).

## Testing a plugin

`tests/test_plugins_and_stores.py` registers fake entry points with a small helper and runs real
requests through the app: copy that pattern. The backend contract tests in `tests/contract/` replay
recorded server streams against the client and are the quickest way to check a new OpenAI-compatible
server.
