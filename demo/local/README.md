# Local agent-discovery demonstration

This demo proves that a local Lemonade inference engine can be exposed as an
A2A agent, discovered through an Agent Card, and called by an independent client.
The core demonstration does not require a cloud account or public registry.

## Preparation

Start Lemonade with a text generation model loaded (see the main README for
version-specific Lemonade commands). In another shell:

```bash
python -m pip install -e '.[dev]'
lemonade-a2a doctor
lemonade-a2a serve --host 127.0.0.1 --port 9100
```

In a third shell:

```bash
python demo/local/demo_local_a2a.py
python demo/local/a2a_client.py
python scripts/validate_real_lemonade.py
```

The demonstration client prints the Agent Card's name and skill identifiers,
then performs a real JSON-RPC `SendMessage`. It fails nonzero on an invalid card,
an HTTP error, or an A2A error. Test the mock server first if Lemonade hardware
is unavailable (see the existing E2E workflow).

## Talk track (5 minutes)

**0:00 — problem.** OpenAI-compatible endpoints expose inference, not agent
discovery, lifecycle and cross-framework task interoperability.

**0:45 — local architecture.** Show the adapter above Lemonade's model router:
the adapter does not load models and works on CPU/GPU/NPU.

**1:30 — Agent Card.** Open `http://127.0.0.1:9100/.well-known/agent-card.json`.
Show identity, skills and protocol declarations.

**2:15 — independent A2A client.** Run `python demo/local/demo_local_a2a.py`.
Point out the unique message ID, JSON-RPC envelope, and returned A2A artifact/task.

**3:00 — proof beyond hello world.** Show an existing streaming/cancel integration
test and the official TCK evidence. Avoid claiming that this small script itself
tests streaming or cancellation.

**4:00 — discoverable agents.** Explain NANDA as an optional ID-to-Agent-Card
resolution layer. A NANDA index entry can point to the Agent Card; routing and
communication remain A2A, inference remains Lemonade. Actual NANDA registration
and remote traversal require a separately verified integration, not this demo.

**4:45 — close.** Highlight offline-first privacy, independent A2A clients,
conformance evidence and a future native Lemonade integration path.

## Next integration milestones

1. Add a `DiscoveryProvider` with static URL and NANDA implementations.
2. Start two different agents and demonstrate agent-to-agent delegation with a
   correlation ID and OpenTelemetry traces.
3. Add contract tests for agent-card resolution, stale entries, SSRF protection,
   authentication and offline fallback.
4. Publish a recorded version-pinned benchmark and demo environment.

Never register a local loopback URL in a public registry as though it were
internet-accessible. For remote demonstrations, explicitly use authenticated
tunneling and a controlled exposure profile.
