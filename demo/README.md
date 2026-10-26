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

`RemoteA2AClient.send_message(parts)` handles protocol 0.3 JSON-RPC messages.
`send_data(action, inputs)` is only a convenience wrapper for remote agents
that document the `action/input` DataPart convention (including Travel Merchant).
Avoid adding merchant-specific endpoints or booking behavior to the library.

The outbound 0.3 client is an early opt-in API, not a complete multi-version
A2A client. Do not imply 1.x interoperability before adding version negotiation
and task lifecycle support.
