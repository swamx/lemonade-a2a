# Architecture

## Objective

Lemonade A2A adds an Agent2Agent protocol boundary to Lemonade without turning the adapter into an agent framework or inference runtime.

The architectural rule is:

> **A2A owns interoperability; Lemonade owns inference.**

## Context

```text
┌───────────────────────────────────────────────────────────────┐
│                      Agent ecosystem                          │
│ ADK / LangGraph / Semantic Kernel / custom / other A2A       │
└─────────────────────────────┬─────────────────────────────────┘
                              │ A2A
                              ▼
┌───────────────────────────────────────────────────────────────┐
│                      Lemonade A2A                             │
│                                                               │
│  Discovery ─ Message mapping ─ Task state ─ Artifacts ─ SSE   │
└─────────────────────────────┬─────────────────────────────────┘
                              │ local HTTP
                              ▼
┌───────────────────────────────────────────────────────────────┐
│                         Lemonade                              │
│          model lifecycle / inference / acceleration           │
└─────────────────────────────┬─────────────────────────────────┘
                              │
                     CPU / GPU / NPU
```

## Components

### Agent Card provider

Produces the A2A discovery document from explicit configuration and, in later phases, capabilities queried from Lemonade. The MVP must not claim skills simply because a model name suggests them.

### Protocol adapter

Converts A2A messages into the minimal request required by Lemonade and converts generated output back to A2A-native message/artifact structures.

### Task manager

Maintains A2A task lifecycle independently from model execution. The initial implementation should use bounded in-memory state. Persistence is optional and belongs behind an interface.

### Lemonade client

A deliberately small client targeting Lemonade's OpenAI-compatible API. It supports non-streaming first and isolates Lemonade-specific behavior from A2A SDK types.

### Conversation memory

Keeps the last turns of each A2A `contextId` per authenticated caller, so a follow-up reaches the model with what came before (`conversations.py`). Bounded by turns, characters, number of conversations and an idle time-to-live; in memory only. It also records, per conversation, each remote agent's own `contextId` and any remote task waiting for input.

### Delegation (outbound)

Optional (`LEMONADE_A2A_MARKETPLACE`). The executor becomes `MarketplaceAgentExecutor` (`delegation.py`): it reads a marketplace's agent list (`discovery/marketplace.py`, `discovery/dns.py`), lets the local model plan which listed agents to ask, calls them through the official A2A SDK client (`client/sdk.py`), and streams the answer composed from their replies. All outbound HTTP goes through `client/_ssrf_transport.py`, which resolves DNS once, refuses private addresses unless allowed, and connects only to the vetted addresses. The remote calls run inside the local task, so its deadline, cancellation and concurrency cap apply to them. See [marketplace.md](marketplace.md).

```text
A2A client ──► agent (this adapter) ──► Lemonade      (inference, including planning)
                    │
                    └──► marketplace listing ──► remote A2A agents (A2A SDK client)
```

### Streaming bridge

Maps Lemonade SSE deltas into A2A streaming events while preserving ordering and cancellation. Backpressure comes from the A2A SDK's bounded event queue: when a consumer stops reading, the producer blocks and stops pulling tokens from Lemonade, and the per-task deadline ends the task. A stream that fails part-way closes its artifact before the task becomes `FAILED`. Disconnect propagation is a policy choice: by default a client leaving does not cancel its task, and `LEMONADE_A2A_CANCEL_ON_DISCONNECT=1` cancels the task started by a streaming request when that request's client goes away.

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | `Settings` loaded from environment, validated at construction, including the exposure-profile rules |
| `server.py` | FastAPI app, Agent Card, A2A routes (base URL plus legacy `/a2a/*` paths), middleware wiring, listener options (TLS / mutual TLS), startup warnings |
| `middleware.py` | Authentication (named API keys → user), rate limiting, body-size limit, UTF-8 and content-type checks, client-disconnect signal, security headers |
| `call_context.py` | Builds the A2A call context: the authenticated user (task owner) and the disconnect event |
| `safe_urls.py` | Tested SSRF policy that must gate any future URL fetch or push-notification callback; unused while those features are off |
| `telemetry.py`, `logging_setup.py` | OpenTelemetry facade (no-op when off) with lazy SDK setup, and JSON logging with trace ids ([observability.md](observability.md)) |
| `registry.py`, `spec/` | The feature registry and compatibility manifest (JSON, shipped in the wheel) and the code that reads and resolves them ([specification.md](specification.md)) |
| `compat.py`, `sdk_gap.py`, `cli.py` | `doctor` checks and probes, the SDK-gap report, and the `lemonade-a2a` command (`serve`, `agents`, `doctor`, `capabilities`, `config`, `support-bundle`) |
| `plugins.py`, `builtin.py`, `stores.py` | Extension API v1 (discovery, loading, version check), the built-in implementations of each extension point, and the persistent SQLite task store ([extending.md](extending.md)) |
| `executor.py` | A2A `AgentExecutor`: input validation (text, opt-in file and data parts), conversation history, Task/Artifact events, `input-required`, cancellation, backend-error to `FAILED` mapping |
| `delegation.py` | `MarketplaceAgentExecutor`: plans and runs calls to marketplace agents, multi-turn remote sessions, compose or relay |
| `conversations.py` | `ConversationStore`: bounded per-caller conversation memory and remote-session state |
| `discovery/` | `marketplace.py` (listings to validated cards), `dns.py` (SRV/TXT lookup), `cards.py` (card validation), `__init__.py` (URL policy, pinned DNS resolution, `StaticDiscovery`) |
| `client/` | Outbound A2A: `sdk.py` (`A2AClient` over the official SDK), `models.py` (`AgentReply`), `errors.py` (typed failures), `_ssrf_transport.py` (guarded transport), `compat_v03.py` (temporary 0.3 JSON-RPC client) |
| `lemonade_client.py` | Pooled OpenAI-compatible client (`chat`, SSE `stream`); no A2A types |
| `task_store.py` | `BoundedTaskStore`: in-memory store evicting the oldest finished tasks |
| `tck/tck_sut.py` | Test-only: real server layer + TCK scenario executor for conformance runs |

## Dependency direction

```text
A2A SDK
   │
   ▼
server / protocol adapters
   │
   ▼
execution core
   │
   ▼
Lemonade client
   │
   ▼
Lemonade server
```

The Lemonade client must not depend on A2A SDK types. This makes future native integration and protocol upgrades easier.

The outbound side is a separate branch that the execution core calls into: `delegation` uses `discovery` and `client`, and `client` uses the A2A SDK's client. Neither `discovery` nor `client` depends on the server, the executor or the Lemonade client, so they can be used on their own (for example from the `agents` command or an application).

## Deployment modes

### Sidecar/reference implementation

```text
A2A client → lemonade-a2a :9100 → Lemonade :13305
```

The adapter defaults to port 9100 because a stock Lemonade install already uses port 9000 for its WebSocket.

This is the initial implementation because it allows fast standards validation without modifying upstream Lemonade.

### Several agents on one machine

```text
                coordinator :9100 ──► marketplace listing
                   │       │
     specialist :9101   specialist :9102
                   └───────┴──► one Lemonade :13305
```

Each agent is one adapter process with its own name, port and card; all of them share one Lemonade server, so there is still one model runtime. A walk-through is in the [multi-agent pathway](pathways/multi-agent.md).

### Native Lemonade protocol surface

Long-term target:

```text
                 Lemonade Server
        ┌──────────────┴──────────────┐
   inference APIs                  A2A API
        └──────────────┬──────────────┘
                    runtime
```

The sidecar should therefore avoid assumptions that would prevent its adapters from being moved into the Lemonade server later.

## Capability model

A2A skills describe agent capabilities, not hardware topology. CPU/GPU/NPU information should therefore be exposed only through optional namespaced extensions or operational endpoints, never by changing core A2A semantics.

The rule is enforced by tests rather than left as intent:

- the Agent Card text never names an accelerator, engine or vendor, and is identical whichever model or backend Lemonade runs (`tests/test_agent_card.py`);
- any Agent Card extension must be `required: false`, so a client that does not know it still works;
- the generic design documents (architecture, protocol mapping, security, conformance) use the term **local AI system** and name no hardware vendor; vendor-specific material lives only in the validation records (`tests/test_repo_hygiene.py`).

Hardware information that is useful to operators (which llama.cpp backend served a run, memory use) is recorded with the benchmark and validation evidence, not in protocol messages.

Potential future skills include text reasoning, coding, vision, speech, embeddings, or image generation when the underlying Lemonade configuration can actually provide them.

## Lightweight target

The protocol process does not load model weights. Its memory is limited to HTTP/A2A libraries, bounded task state, configuration, and active streams.

A future Rust/Go implementation can target a very small resident footprint, but no `<10 MB` claim should be made until measured on supported platforms.

## Policy boundary

The adapter may enforce A2A-facing security and resource limits, but model selection,
backend routing and inference belong to Lemonade. Do not add a parallel reasoning or
model-routing layer here. Delegation does not break this rule: it chooses which *remote agent*
to ask, and makes that choice by asking the Lemonade model, never by picking a model or a
backend itself.

Core interoperability must not depend on deployment-specific policies or hardware metadata.
