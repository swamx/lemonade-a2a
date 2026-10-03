# Lemonade A2A 🍋

**A first-class Agent2Agent (A2A) protocol surface for Lemonade local AI systems.**

Lemonade A2A is an open-source reference implementation and upstream-oriented proposal for exposing models served by [Lemonade](https://github.com/lemonade-sdk/lemonade) as standards-compliant [Agent2Agent (A2A)](https://github.com/a2aproject/A2A) agents.

> **If Lemonade can serve intelligence locally, it should be able to expose that intelligence as an interoperable A2A agent.**

The project intentionally uses **local AI systems** rather than **AMD AI PCs** as its platform concept. Lemonade is built for local AI across PCs and operating systems, with multiple CPU/GPU/NPU backends including AMD, NVIDIA, Apple Silicon, Vulkan and CPU execution. AMD acceleration remains an important optimization target, not a protocol requirement.

## Why?

OpenAI-compatible APIs standardize **model inference**. A2A standardizes **agent interoperability**. Lemonade A2A adds the latter without inventing another agent framework or inference runtime.

```text
Application / Agent Framework
          │
          ├──── OpenAI-compatible API ───► Model inference
          │
          └──── A2A ─────────────────────► Agent interoperability
                                               │
                                               ▼
                                            Lemonade
                                               │
                                  Local AI System Runtime
                                               │
                                    CPU / GPU / NPU backends
```

## Goals

- A2A v1-compatible protocol surface for Lemonade-hosted intelligence.
- Automatic Agent Card generation.
- A2A Message/Task/Artifact ↔ Lemonade inference translation.
- Streaming and cancellation propagation.
- Local-first operation with no mandatory cloud dependency.
- Framework and hardware independence.
- Thin protocol layer: this process does **not** load AI models.
- Preserve Lemonade's backend abstraction rather than binding A2A to a particular accelerator.
- Upstream-friendly architecture that can evolve toward native Lemonade integration.

## Architecture

```text
                    A2A Clients / Agent Frameworks
                               │
                            A2A v1
                               │
                               ▼
                  ┌─────────────────────────┐
                  │      Lemonade A2A       │
                  │                         │
                  │  Agent Card / Discovery│
                  │  Message Adapter        │
                  │  Task / Artifact Map    │
                  │  Streaming / Cancel     │
                  │  Security Policy        │
                  └────────────┬────────────┘
                               │
                       OpenAI-compatible
                       Lemonade endpoint
                               │
                               ▼
                  ┌─────────────────────────┐
                  │        Lemonade         │
                  │ Model / backend routing │
                  │ Local inference runtime │
                  └────────────┬────────────┘
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
            CPU               GPU               NPU
             │                 │                 │
             └────────── Local AI System ────────┘
```

A2A should remain above Lemonade's model/backend router. It should not know whether inference is ultimately executed by ROCm, CUDA, Vulkan, Metal, CPU, Ryzen AI or another backend. This keeps the protocol surface aligned with Lemonade's vision: standard local AI APIs with hardware-specific optimization behind the server boundary.

See [docs/architecture.md](docs/architecture.md).

## Current validation status

The reference implementation now has the major MVP pieces in place:

- official A2A SDK integration and `AgentExecutor`;
- Agent Card discovery;
- JSON-RPC and HTTP+JSON A2A surfaces;
- Lemonade OpenAI-compatible backend client;
- deterministic mock-Lemonade end-to-end testing;
- SSE token streaming into A2A artifacts;
- active cancellation propagation into the running inference coroutine;
- CI across supported Python versions;
- A2A conformance checks;
- direct-vs-A2A latency benchmark tooling.

The latest `main` CI and deterministic E2E workflows are green. The next validation milestone is **real Lemonade runtime validation**, followed by upstream-native design work.

## North-star developer experience

The north star is for A2A to become another standard Lemonade server surface rather than a permanently separate inference stack:

```bash
lemonade serve --a2a
```

Conceptually:

```text
OpenAI API   http://localhost:13305/v1/...
A2A          http://localhost:13305/a2a/...
Agent Card   http://localhost:13305/.well-known/agent-card.json
```

The exact CLI and paths above are design targets, not claims about current upstream Lemonade behavior.

## MVP

1. **Discovery** — generate and serve an Agent Card.
2. **Messaging** — translate A2A messages into Lemonade inference requests.
3. **Execution** — expose Lemonade inference through an A2A `AgentExecutor`.
4. **Artifacts** — return generated content using A2A-native messages/artifacts.
5. **Streaming** — preserve incremental local generation.
6. **Lifecycle** — propagate cancellation and map failures/timeouts correctly.
7. **Compatibility** — validate against official A2A tooling/TCK.
8. **Runtime validation** — prove the same surface against a real Lemonade installation and multiple Lemonade backends.

## Quick start

> Pre-alpha reference implementation. Pinning and API details may change while A2A v1 integration is validated.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .

export LEMONADE_BASE_URL=http://localhost:13305/v1
export LEMONADE_MODEL=your-model
lemonade-a2a
```

The adapter expects a running Lemonade server exposing an OpenAI-compatible endpoint.

## Design principles

**Standards first.** Use A2A rather than creating a proprietary agent protocol.

**Thin data plane.** A2A translation belongs here; model inference and backend selection belong in Lemonade.

**Local first.** A complete interaction can remain on the local AI system or trusted LAN.

**Hardware agnostic at the protocol boundary.** A2A clients should not need to know whether Lemonade selected CPU, GPU or NPU execution.

**Untrusted by default.** Agent Cards, messages, files, URLs, metadata and artifacts are external input.

**Upstream friendly.** Keep protocol boundaries explicit so the reference implementation can inform a future native Lemonade A2A surface.

## What this project is not

Lemonade A2A is **not a new agent reasoning framework** and does not require special "System One" models such as CLM, LAYA or JEV. Model intelligence remains a Lemonade concern. The A2A layer is responsible for interoperability, lifecycle, streaming, discovery and policy—not inventing a second model-routing or reasoning stack.

If Lemonade later gains richer routing, model selection or scheduling capabilities, A2A should consume those capabilities through Lemonade rather than duplicate them here.

## Next architecture milestone

The next step is to move from **reference implementation validation** toward an **upstream-native A2A surface**:

```text
Today

A2A Client
    │
    ▼
Python Lemonade A2A reference adapter
    │
    ▼
OpenAI-compatible Lemonade API
    │
    ▼
Lemonade Router → backend → local hardware

Next

A2A Client                    OpenAI / Anthropic / Ollama clients
    │                                      │
    └──────────────┬───────────────────────┘
                   ▼
             Lemonade Server
                   │
        ┌──────────┴──────────┐
        │ Protocol/API layer  │
        │ OpenAI | ... | A2A │
        └──────────┬──────────┘
                   │
             Lemonade Router
                   │
          Model/backend manager
                   │
          CPU / GPU / NPU backends
```

Before implementing the native C++ surface, the reference adapter should pass real-runtime, lifecycle, conformance and overhead gates. That gives the upstream proposal measurable evidence rather than only an architectural concept.

## Recommended next steps

1. Run the black-box suite against a **real Lemonade Server**, not only Mock Lemonade.
2. Add **TTFT and streaming overhead** to the benchmark; latency should be compared against direct Lemonade inference.
3. Add timeout/failure mapping and bounded streaming/backpressure tests.
4. Run and record the official A2A TCK/Inspector compatibility matrix.
5. Validate at least two materially different Lemonade backend configurations to prove hardware independence.
6. Draft the native Lemonade A2A API boundary and map it onto Lemonade's existing HTTP/router architecture.
7. Only then prototype the native C++ A2A endpoint and propose it upstream.

See [docs/roadmap.md](docs/roadmap.md) for the tracked TODO list.

## Documentation

- [Architecture](docs/architecture.md)
- [Protocol mapping](docs/protocol-mapping.md)
- [Security](docs/security.md)
- [Conformance](docs/conformance.md)
- [Roadmap / TODO](docs/roadmap.md)
- [Upstream integration](docs/upstream-integration.md)
- [AMD Lemonade Challenge](docs/amd-challenge.md)

## Status

**Experimental / pre-alpha.** The reference implementation now validates the core integration boundary. The project is moving into real-runtime validation, hardening and upstream-native design.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Acknowledgements

Built on the open-source work of the Lemonade community and the Linux Foundation Agent2Agent project. This repository is an independent community project and is not an official AMD or Linux Foundation product.
