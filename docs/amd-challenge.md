# AMD Lemonade Challenge Proposal

## Lemonade A2A — Make Every AI PC an Interoperable Agent

### One-line pitch

**Add the open Agent2Agent standard to Lemonade so any locally served model can participate in heterogeneous agent systems without sending inference to the cloud.**

## Problem

Local inference is increasingly easy, but agent interoperability remains fragmented. A developer may run an excellent model through Lemonade and still need framework-specific glue before another agent can discover its capabilities, send work, track a task, stream progress, or consume artifacts.

OpenAI compatibility solves the model API problem. A2A addresses the agent interoperability problem.

## Proposal

Lemonade A2A is a reference implementation and upstream-oriented design for a first-class A2A protocol surface in Lemonade.

```text
Agent ecosystem
      │
     A2A
      ▼
Lemonade A2A
      │
  Lemonade
      │
AMD CPU/GPU/NPU
```

A user should eventually be able to start Lemonade with A2A enabled and immediately obtain an Agent Card and A2A endpoint for the configured local capability.

## Why it fits Lemonade

### Community impact

A standard protocol surface lets A2A-compatible frameworks and agents reuse local Lemonade intelligence without custom integrations. The contribution is infrastructure for the ecosystem rather than a single-purpose application.

### Technical depth

The project must correctly bridge two different abstractions:

- agent discovery and capability declaration;
- asynchronous task lifecycle;
- message and artifact translation;
- streaming and cancellation;
- local model inference;
- security/resource limits;
- compatibility testing.

### Creativity

The project treats the AI PC not merely as a chatbot endpoint but as a **standards-addressable agent node**. Local AMD-powered intelligence becomes discoverable and composable with other agents while retaining local inference and privacy.

## Demo

Two independently implemented agents run on separate processes or machines.

```text
External A2A Agent
        │
        │ discover Agent Card
        ▼
Lemonade A2A on AMD PC
        │
        │ local inference
        ▼
Qwen/Gemma/etc. via Lemonade
        │
        │ A2A response/stream
        ▼
External Agent
```

Demo counters:

- cloud inference calls: `0`;
- A2A messages exchanged;
- model used;
- task latency;
- streaming time-to-first-token;
- local device/runtime information where available.

The important demonstration is interoperability: the external agent does not need Lemonade-specific code.

## Benchmark plan

Measure adapter overhead separately from model inference:

1. direct Lemonade request;
2. same request through Lemonade A2A;
3. non-streaming latency overhead;
4. streaming time-to-first-token overhead;
5. adapter idle/active CPU and RSS;
6. concurrent A2A tasks;
7. A2A compatibility test results.

This prevents model speed from hiding protocol-layer inefficiencies.

## Stretch innovation

Once core A2A support is compliant, the same boundary enables research features:

### System-One routing

CLM/LAYA-style decision models can rank agents or actions without spending a generative LLM call on every routing decision.

### Speculative delegation

A cheap predictor can begin a high-confidence A2A branch while a slower planner is still reasoning, reducing end-to-end tool/agent latency when prediction is correct.

### Heterogeneous scheduling

Agent capabilities can be paired with Lemonade models and local AMD compute to optimize latency, quality, energy, or privacy constraints.

These remain optional extensions. The core contribution is standards-compliant A2A interoperability for Lemonade.

## Evidence so far

- A real Lemonade 2026.40.0 + llama.cpp (GPU and CPU) run passes the A2A end-to-end validator.
- Measured adapter overhead on that setup: about +5 ms (GPU) and +11 ms (CPU) time-to-first-token, with unchanged streaming throughput and working cancellation.
- Adapter footprint: about 69 MB RSS and near-zero idle CPU.
- Official A2A TCK against the protocol surface: 157 passed, 0 failed, 4 expected-fail (SHOULD), rest skipped for undeclared capabilities.
- Measured on llama.cpp CUDA, Vulkan and CPU with a 1.7B and a 4B model; concurrent load up to 8 requests shows flat throughput (backend-bound) and no adapter-added cost.
- Not yet demonstrated: ITK/cross-SDK interoperability, NPU/ROCm (AMD) backends, or a cross-machine demo.

Details and caveats: [benchmarks.md](benchmarks.md).

## Success criteria

A strong challenge submission should demonstrate:

- a working A2A client talking to Lemonade-hosted local inference;
- automatic Agent Card discovery;
- streaming;
- correct task lifecycle;
- published compatibility-test results;
- measured adapter overhead;
- a documented path toward upstream integration.

## Long-term vision

```text
lemonade-server serve --a2a
```

should be enough to turn an AMD AI PC into an interoperable local agent endpoint.
