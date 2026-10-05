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
             └────────── Local AI  ────────┘
```

A2A should remain above Lemonade's model/backend router. It should not know whether inference is ultimately executed by ROCm, CUDA, Vulkan, Metal, CPU, Ryzen AI or another backend. This keeps the protocol surface aligned with Lemonade's vision: standard local AI APIs with hardware-specific optimization behind the server boundary.

See [docs/architecture.md](docs/architecture.md).

## Current status

Validated so far (details: [roadmap](docs/roadmap.md), [benchmarks](docs/benchmarks.md)):

- official A2A SDK integration with JSON-RPC and HTTP+JSON bindings served at the base URL (legacy `/a2a/jsonrpc` and `/a2a/rest` paths kept);
- automatic Agent Card, SSE streaming into A2A artifacts, cancellation that stops the real backend generation, backend failures mapped to `FAILED` tasks;
- resource bounds (input size/parts, per-task deadline, concurrency cap with `REJECTED`, bounded task store, slow-consumer backpressure), exposure profiles, API-key auth with several named callers and per-user task isolation, rate limiting, TLS and mutual TLS, graceful shutdown, optional cancel-on-disconnect;
- property-based fuzzing of messages, parts, metadata and backend stream output (it found and fixed two real defects);
- the **official A2A TCK** against the protocol surface: 157 passed, 0 failed, 4 expected-fail (SHOULD), the rest skipped for capabilities not declared (see [conformance](docs/conformance.md) for scope);
- **independent clients** (A2A CLI, and the JavaScript, Go, .NET and Java SDKs, plus the Inspector's validators) pass over JSON-RPC and HTTP+JSON, on the mock and on real Lemonade ([interoperability](docs/interoperability.md));
- CI on Python 3.11-3.13 (ruff, ruff format, pytest) plus a deterministic mock-Lemonade end-to-end job;
- a **real Lemonade 2026.40.0** setup on llama.cpp CUDA, Vulkan and CPU with a 1.7B and a 4B model: the validator and an opt-in pytest suite pass, A2A adds roughly 5-15 ms TTFT within a defined overhead budget on those models (a 12B model on CUDA was over budget, likely noise, and is only partly measured), and concurrent load (up to 8) shows no adapter-added cost (throughput is bounded by the backend).

Not yet done: ITK (it tests SDKs against each other, so it does not apply to a standalone adapter), NPU/ROCm/Metal and non-llama.cpp backends (no hardware available so far), OAuth/OIDC inside the adapter (by decision, done at a gateway), URL/file fetching (disabled; its safety policy is written and tested first). The next milestone is cross-backend evidence on AMD hardware and the upstream-native design.

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
lemonade-a2a                    # serves on http://127.0.0.1:9100
```

The adapter expects a running Lemonade server exposing an OpenAI-compatible endpoint. It listens on **9100** by default because a stock Lemonade install already uses port 9000 for its WebSocket.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LEMONADE_BASE_URL` | `http://localhost:13305/v1` | Lemonade OpenAI-compatible endpoint |
| `LEMONADE_MODEL` | empty (server default) | Model id sent to Lemonade |
| `LEMONADE_A2A_HOST` / `LEMONADE_A2A_PORT` | `127.0.0.1` / `9100` | Listen address |
| `LEMONADE_A2A_PUBLIC_URL` | `http://localhost:9100` | URL advertised in the Agent Card; keep in sync with host/port |
| `LEMONADE_A2A_AGENT_NAME` / `LEMONADE_A2A_AGENT_DESCRIPTION` | see `config.py` | Agent Card text |
| `LEMONADE_TIMEOUT_SECONDS` | `120` | Backend request timeout |
| `LEMONADE_A2A_MAX_INPUT_CHARS` / `LEMONADE_A2A_MAX_INPUT_PARTS` | `100000` / `32` | Maximum input text / message parts |
| `LEMONADE_A2A_MAX_REQUEST_BYTES` | `1048576` | Maximum HTTP request body (413 above it) |
| `LEMONADE_A2A_MAX_TASK_SECONDS` | `600` | Per-task deadline (task fails when exceeded) |
| `LEMONADE_A2A_MAX_CONCURRENT_TASKS` | `8` | Running-task cap; extra tasks are `REJECTED` |
| `LEMONADE_A2A_MAX_STORED_TASKS` | `1000` | Task store size; oldest finished tasks are evicted |
| `LEMONADE_A2A_PROFILE` | `local` | How the adapter is exposed: `local` (loopback only), `lan` (needs auth), `external` (needs auth + TLS + rate limit). A mismatch stops startup, see [docs/security.md](docs/security.md) |
| `LEMONADE_A2A_API_KEY` | empty (no auth) | Require `Authorization: Bearer` / `X-API-Key`; Agent Card and `/healthz` stay public |
| `LEMONADE_A2A_API_KEYS` | empty | Several callers: `alice:key1,bob:key2`. Each name owns its tasks; others cannot see them |
| `LEMONADE_A2A_RATE_LIMIT_PER_MINUTE` | `0` (off) | Per-identity request budget; HTTP 429 with `Retry-After` beyond it |
| `LEMONADE_A2A_CANCEL_ON_DISCONNECT` | `0` | Cancel a task when the streaming client that started it disconnects |
| `LEMONADE_API_KEY` | empty | Key sent to a protected Lemonade server |
| `LEMONADE_A2A_SSL_CERTFILE` / `LEMONADE_A2A_SSL_KEYFILE` | empty | Serve HTTPS (set both; use an `https://` public URL) |
| `LEMONADE_A2A_SSL_CA_CERTS` / `LEMONADE_A2A_SSL_REQUIRE_CLIENT_CERT` | empty / `0` | Mutual TLS: refuse clients without a certificate signed by this CA |

Invalid values fail at startup. See [examples/](examples/) for an Agent Card and a client, and [docs/real-lemonade-validation.md](docs/real-lemonade-validation.md) for validating against a real Lemonade install.

## Design principles

**Standards first.** Use A2A rather than creating a proprietary agent protocol.

**Thin data plane.** A2A translation belongs here; model inference and backend selection belong in Lemonade.

**Local first.** A complete interaction can remain on the local AI system or trusted LAN.

**Hardware agnostic at the protocol boundary.** A2A clients should not need to know whether Lemonade selected CPU, GPU or NPU execution.

**Untrusted by default.** Agent Cards, messages, files, URLs, metadata and artifacts are external input.

**Upstream friendly.** Keep protocol boundaries explicit so the reference implementation can inform a future native Lemonade A2A surface.

## What this project is not

Lemonade A2A is **not a new agent reasoning framework**. Model intelligence remains a Lemonade concern. The A2A layer is responsible for interoperability, lifecycle, streaming, discovery and policy—not inventing a second model-routing or reasoning stack.

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

1. Re-run the 12B benchmark cells on a quieter machine and diagnose the direct-path "no text chunks" failure; review the first GitHub runs of the TCK and benchmark jobs.
2. Validate on AMD hardware: NPU and ROCm paths and a non-llama.cpp engine (not possible on the NVIDIA/Intel machine used so far).
3. Persist tasks (the store is in memory) and decide whether the adapter should read an OIDC token subject for task ownership.
4. File the drafted upstream TCK issue (needs owner approval).
5. Draft the native Lemonade A2A API boundary and map it onto Lemonade's HTTP/router architecture.
6. Only then prototype the native C++ A2A endpoint and propose it upstream.

See [docs/roadmap.md](docs/roadmap.md) for the tracked TODO list.

## Documentation

- [Architecture](docs/architecture.md)
- [Protocol mapping](docs/protocol-mapping.md)
- [Security](docs/security.md)
- [Conformance](docs/conformance.md)
- [Interoperability](docs/interoperability.md)
- [Real Lemonade validation](docs/real-lemonade-validation.md)
- [Benchmarks](docs/benchmarks.md)
- [Roadmap / TODO](docs/roadmap.md)
- [Upstream integration](docs/upstream-integration.md)
- [AMD Lemonade Challenge](docs/amd-challenge.md)

## Status

**Experimental / pre-alpha.** The protocol surface passes the official TCK, the integration is validated against a deterministic mock and a real Lemonade server, and the adapter has basic resource bounds and optional auth/TLS. The project is moving into AMD-hardware evidence, interoperability testing and upstream-native design.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Acknowledgements

Built on the open-source work of the Lemonade community and the Linux Foundation Agent2Agent project. This repository is an independent community project and is not an official AMD or Linux Foundation product.
