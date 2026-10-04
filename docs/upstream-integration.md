# Upstream Integration Plan

This repository is both a runnable reference implementation and an executable specification for a future native Lemonade A2A protocol surface.

## Rule

Do not upstream the Python sidecar wholesale.

The Python implementation exists to validate A2A behavior, compatibility, lifecycle semantics and benchmarks quickly. Once behavior is stable, port only the protocol adapter into Lemonade's native server architecture.

## Target Lemonade layout

Based on the current Lemonade repository structure, the intended patch shape is:

```text
lemonade/
├── src/cpp/include/lemon/
│   └── a2a_api.h
├── src/cpp/server/
│   ├── a2a_api.cpp
│   └── server.cpp            # route registration only
├── tests/                    # native A2A tests in upstream convention
└── docs/                     # A2A endpoint documentation
```

A2A should resemble Lemonade's existing protocol adapters (Anthropic, Ollama and MCP): protocol translation lives in its own module and delegates model execution to existing Lemonade routing/backend infrastructure.

## What maps upstream

| Reference implementation | Native Lemonade destination |
|---|---|
| `server.py` | `server.cpp` registration + `a2a_api.cpp` |
| `executor.py` | A2A request/task state logic in `a2a_api.cpp` |
| `lemonade_client.py` | **Removed**; native adapter calls Router/backend infrastructure directly |
| `agent_card.py` | Agent Card serializer/capability mapping |
| `config.py` | Lemonade CLI/config conventions |
| Python tests | equivalent native/API integration tests |
| TCK scripts | retained as black-box interoperability tests |

## Non-negotiable Lemonade invariants

The native implementation must preserve Lemonade's existing requirements:

- API-key enforcement when configured;
- many-clients-one-server topology;
- inference backends remain subprocesses;
- cross-platform Windows/Linux/macOS behavior;
- thread-safe concurrent request handling;
- cancellation/disconnect behavior must not block shared Router locks;
- protocol-defined A2A paths should be treated explicitly rather than accidentally inheriting unrelated OpenAI URL semantics.

## Upstream PR sequence

Avoid a giant challenge PR. Prefer independently reviewable patches:

1. **Design proposal** — Agent Card, transports, route shape, security and Router interaction.
2. **Agent Card + non-streaming JSON-RPC** — smallest useful native vertical slice.
3. **Streaming + cancellation** — SSE/task lifecycle and disconnect propagation.
4. **HTTP+JSON / additional bindings** — only after the core is stable.
5. **Compatibility evidence** — A2A TCK/Inspector results and benchmark report.

Separate reasoning/routing layers and challenge-specific UI/demo code must not be prerequisites for the upstream A2A protocol patch.

## Executable-specification contract

Before porting a behavior to C++, add a black-box test against the Python reference. The same test vector should then run against native Lemonade.

```text
A2A test vector
      ├────────► Python reference
      │              │
      │           expected
      │
      └────────► Native Lemonade
                     │
                  same behavior
```

This lets the challenge project innovate quickly while keeping the upstream contribution small and maintainable.
