# Demos

Runnable demonstrations are isolated from the reusable `lemonade_a2a` package.

- [Local A2A](local/README.md) — discover and call the running Lemonade adapter.
- [AMD Travel Merchant](travel/README.md) — connect the local Lemonade model
  to an independently hosted travel agent over outbound A2A.

From the repository root, install with `python -m pip install -e ".[dev]"`
and run scripts using their full `demo/<scenario>/...` paths.

Production and reusable protocol code belongs in `src/lemonade_a2a/`.
CI/testing utilities belong in `scripts/`, library tests in `tests/`,
and scenario assets and instructions belong here.

## Stable API boundary

`A2AClient.send_message(request)` handles protocol 0.3 JSON-RPC messages.
`client.compat_v03.RemoteA2AClient.send_data(action, inputs)` is only a convenience wrapper for remote agents
that document the `action/input` DataPart convention (including Travel Merchant).
Avoid adding merchant-specific endpoints or booking behavior to the library.

`client.compat_v03.RemoteA2AClient` is a temporary 0.3-only client for the Travel
Merchant demo. The general outbound API is `lemonade_a2a.client.A2AClient` (the official
SDK client: 1.x and 0.3 cards, JSON-RPC and HTTP+JSON, task polling, `input-required`)
and `lemonade_a2a.discovery.marketplace.Marketplace`; for several local agents that
delegate to each other, follow the [multi-agent pathway](../docs/pathways/multi-agent.md).
