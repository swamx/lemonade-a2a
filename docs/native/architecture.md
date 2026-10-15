# Native architecture

**Status: draft (2026-10-15).** Upstream facts are marked *verified* (at commit `f0816fe`) or *assumed*.

## Placement

A2A becomes one more protocol module registered by `Server::setup_routes`, next to the Ollama, Anthropic and MCP
modules. It translates protocol and delegates execution; it never loads a model or owns a backend.

```text
A2A clients                    OpenAI / Ollama / Anthropic / MCP clients
     │                                         │
     └───────────────────┬─────────────────────┘
                         ▼
            lemond (cpp-httplib server)
        ┌────────────────┴──────────────────┐
        │ route registration (server.cpp)   │
        │  OpenAI | Ollama | Anthropic | MCP | A2A (a2a_api.cpp)
        └────────────────┬──────────────────┘
                         │  EnsureLoaded + Router calls
                         ▼
                      Router ──► WrappedServer (backend subprocesses)
                         ▲
                  ModelManager (model list, metadata)
```

The closest existing module is `McpServer` (*verified*): a class constructed with `(Router*, ModelManager*,
EnsureLoadedFn)`, a `register_routes(httplib::Server&)` that must run on a `shared_ptr`, a hand-written JSON-RPC
dispatcher over `nlohmann::json`, and an explicit authentication exception. `A2aApi` should copy that shape.
No new third-party dependency is needed: the A2A JSON-RPC and HTTP+JSON bindings are JSON over HTTP and SSE, which
`cpp-httplib` and `nlohmann/json` already cover (*assumed*: no maintained C++ A2A SDK was found; re-check before
writing code).

## The portable core

Behavior that must be identical in the Python adapter and in native, because the same black-box suites run
against both. Everything else is implementation detail.

| Core element | Defined by | Notes for native |
|---|---|---|
| Input validation and limits (text only, empty, size, part count) | [protocol-mapping.md](../protocol-mapping.md) behavior table | Same error mapping |
| Task state machine (`SUBMITTED`→`WORKING`→terminal, `REJECTED`, `CANCELED`, `FAILED`) | same | Native owns it, see [state-and-lifecycle.md](state-and-lifecycle.md) |
| Artifact protocol (`lemonade-response`, `lemonade-reasoning`, `last_chunk`, close-before-`FAILED`) | same | Same artifact ids and metadata |
| Error mapping and sanitization (no paths, keys, stack traces) | same | Details to the server log only |
| Agent Card content (no vendor or hardware terms, optional extensions only) | [architecture.md](../architecture.md) | Generated from model state, see [capabilities-and-card.md](capabilities-and-card.md) |
| Cancel and disconnect semantics (disconnect does not cancel by default) | same | Needs an explicit per-task cancel handle |
| Telemetry instrument and span names | [observability.md](../observability.md) §2 | Names are implementation-independent by design |
| Feature ids | [features.md](../features.md) | A native build claims features by the same ids |

## What is not carried over

| Python | Native |
|---|---|
| `lemonade_client.py` (httpx client, SSE parser) | Removed: the module calls the Router in-process. The error-inside-a-200-stream handling moves with it (see below) |
| A2A SDK types, `AgentExecutor`, `EventQueue` | Replaced by hand-written types and a small event log |
| FastAPI, uvicorn, ASGI middleware | `cpp-httplib` route handlers and Lemonade's own `authenticate_request` |
| `plugins.py`, `doctor`, `support-bundle`, `config` CLI | Not applicable: Lemonade's own CLI and `config.json` conventions apply |
| `stores.py` (SQLite) | Optional later; see decision D3 |

Behavior that existed *because* the adapter was a separate process becomes simpler or disappears: no backend
timeout (in-process), no connection pooling, no unreachable-backend state. One thing must be re-established:
Lemonade reports some errors (for example a prompt longer than the loaded context) *inside* a successful stream.
The native stream bridge must keep treating those as task `FAILED`, never as an empty completion.

## Threading and locking

Upstream invariants that bind this design (from [upstream-integration.md](../upstream-integration.md)): thread-safe
concurrent handling, and cancellation must not block shared Router locks.

- **Request threads** (httplib workers) parse, validate, enqueue and, for streaming, relay events. They must not
  be held for a task's whole life when the client may leave and resubscribe.
- **Task runners**: a bounded set (the concurrency cap, default 8 in the adapter) of worker threads, each
  executing one task against the Router. The cap is what produces `REJECTED`.
- **Task record**: one mutex per task guarding state, artifacts and an event log, plus a condition variable so any
  number of subscribers can follow it. The store's own lock is held only for lookup, insert and eviction, never
  while calling the Router.
- **Cancel handle**: one `std::atomic<bool>` per task. *Verified*: `Router::chat_completion` already accepts an
  `std::atomic<bool>* cancel`, and upstream's own handlers derive a `RequestCancelToken` from the connection
  state. The streaming entry point is `Router::chat_completion_stream(request_body, httplib::DataSink&)`, which
  writes to an HTTP sink; whether a task-owned cancel flag can reach that path is the first thing the prototype
  must prove (see the spike list in [decisions.md](decisions.md)).

## Non-goals

- Not a second inference path and not a router of its own.
- No change to existing endpoints or the existing request pipeline.
- No agent reasoning, planning or delegation (that is P4).
- Hardware, accelerator and vendor details never appear in protocol content.
