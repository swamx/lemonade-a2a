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

### Streaming bridge

Maps Lemonade SSE deltas into A2A streaming events while preserving ordering and cancellation. Backpressure and disconnect propagation are requirements, not optional optimizations.

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

## Deployment modes

### Sidecar/reference implementation

```text
A2A client → lemonade-a2a :9000 → Lemonade :8000
```

This is the initial implementation because it allows fast standards validation without modifying upstream Lemonade.

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

Potential future skills include text reasoning, coding, vision, speech, embeddings, or image generation when the underlying Lemonade configuration can actually provide them.

## Lightweight target

The protocol process does not load model weights. Its memory is limited to HTTP/A2A libraries, bounded task state, configuration, and active streams.

A future Rust/Go implementation can target a very small resident footprint, but no `<10 MB` claim should be made until measured on supported platforms.

## Extension architecture

Experimental features sit above or beside the standards adapter:

```text
A2A request
    │
    ▼
Policy / optional router
    │
    ├── System-One decision backend (CLM/LAYA)
    ├── capability scheduler
    └── privacy policy
    │
    ▼
A2A standards adapter
    │
    ▼
Lemonade
```

Core interoperability must continue working when all experimental extensions are disabled.
