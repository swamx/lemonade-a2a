# Lemonade A2A 🍋

**A first-class Agent2Agent (A2A) protocol surface for AMD Lemonade.**

Lemonade A2A is an open-source reference implementation and upstream-oriented proposal for exposing models served by [AMD Lemonade](https://github.com/lemonade-sdk/lemonade) as standards-compliant [Agent2Agent (A2A)](https://github.com/a2aproject/A2A) agents.

> **If Lemonade can serve a model locally, it should be able to expose that intelligence as an interoperable A2A agent.**

## Why?

OpenAI-compatible APIs standardize **model inference**. A2A standardizes **agent interoperability**. Lemonade A2A adds the latter without inventing another agent framework.

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
                                         CPU / GPU / NPU
```

## Goals

- A2A v1-compatible protocol surface for Lemonade-hosted intelligence.
- Automatic Agent Card generation.
- A2A Message/Task/Artifact ↔ Lemonade inference translation.
- Streaming.
- Local-first operation with no mandatory cloud dependency.
- Framework independence.
- Thin protocol layer: this process does **not** load AI models.
- Upstream-friendly architecture that can evolve toward native Lemonade integration.

## Architecture

```text
                         A2A Clients
                             │
                          A2A v1
                             │
                             ▼
                ┌───────────────────────┐
                │     Lemonade A2A      │
                │                       │
                │  Agent Card           │
                │  Message Adapter      │
                │  Task / Artifact Map  │
                │  Streaming            │
                │  Security Policy      │
                └───────────┬───────────┘
                            │
                    OpenAI-compatible
                    Lemonade endpoint
                            │
                            ▼
                ┌───────────────────────┐
                │       Lemonade        │
                │  Local model runtime  │
                └───────────┬───────────┘
                            │
                    CPU / GPU / NPU
```

See [docs/architecture.md](docs/architecture.md).

## North-star developer experience

```bash
lemonade-server serve --a2a
```

Conceptually:

```text
OpenAI API   http://localhost:8000/v1/...
A2A          http://localhost:9000/
Agent Card   http://localhost:9000/.well-known/agent-card.json
```

The CLI and paths above are design targets rather than claims about current upstream Lemonade behavior.

## MVP

1. **Discovery** — generate and serve an Agent Card.
2. **Messaging** — translate A2A messages into Lemonade chat-completion requests.
3. **Execution** — expose Lemonade inference through an A2A AgentExecutor.
4. **Artifacts** — return generated content using A2A-native messages/artifacts.
5. **Streaming** — preserve incremental local generation where supported.
6. **Compatibility** — validate against official A2A tooling/TCK as implementation matures.

## Quick start

> Pre-alpha reference implementation. Pinning and API details may change while A2A v1 integration is validated.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e .

export LEMONADE_BASE_URL=http://localhost:8000/v1
export LEMONADE_MODEL=your-model
lemonade-a2a
```

The adapter expects a running Lemonade server exposing an OpenAI-compatible endpoint.

## Repository

```text
lemonade-a2a/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── pyproject.toml
├── src/lemonade_a2a/
│   ├── __init__.py
│   ├── config.py
│   ├── agent_card.py
│   ├── lemonade_client.py
│   ├── executor.py
│   └── server.py
├── examples/
│   └── agent-card.json
├── tests/
│   └── test_agent_card.py
└── docs/
    ├── architecture.md
    ├── protocol-mapping.md
    ├── security.md
    ├── roadmap.md
    └── amd-challenge.md
```

## Design principles

**Standards first.** Use A2A rather than creating a proprietary agent protocol.

**Thin data plane.** A2A translation belongs here; model inference belongs in Lemonade.

**Local first.** A complete interaction can remain on the machine or trusted LAN.

**Untrusted by default.** Agent Cards, messages, files, URLs, metadata and artifacts are external input.

**Upstream friendly.** Keep protocol boundaries explicit so the work can inform a future native Lemonade A2A surface.

## Beyond the MVP

Once core A2A interoperability is stable:

- OpenAI ↔ A2A bridging.
- Multi-model Agent Cards and skills.
- AMD hardware/capability metadata through extensions.
- Local/LAN privacy policy.
- Lightweight Rust/Go sidecar evaluation.
- System-One decision backends such as CLM/LAYA for routing and ranking.
- Speculative A2A delegation.
- Capability-aware scheduling across local A2A agents.
- Quality/latency/energy benchmarks.

These remain optional extensions rather than changes to the A2A standard.

## Documentation

- [Architecture](docs/architecture.md)
- [Protocol mapping](docs/protocol-mapping.md)
- [Security](docs/security.md)
- [Roadmap](docs/roadmap.md)
- [AMD Lemonade Challenge](docs/amd-challenge.md)

## Status

**Experimental / pre-alpha.** This repository starts as a reference implementation intended to validate the integration boundary and produce an upstream-quality proposal.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Acknowledgements

Built on the open-source work of AMD Lemonade and the Linux Foundation Agent2Agent project. This repository is an independent community project and is not an official AMD or Linux Foundation product.
