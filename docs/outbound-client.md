# Outbound client architecture

The official `a2a-sdk[fastapi]` is the protocol implementation. Do not add
custom JSON-RPC parsing, task state machines, polling, version selection or SSE
decoding to Lemonade-A2A.

## Package ownership

- `client/sdk.py`: public `A2AClient`; SDK card resolution, transport
  selection, message/streaming and task operations; HTTP timeouts and auth.
- `discovery/__init__.py`: `AgentLocation`, `DiscoveryProvider` and `StaticDiscovery`;
  vendor-independent alias -> base URL lookup and first-pass URL policy.
- `discovery/marketplace.py`: `Marketplace`, reads agent listings from a marketplace
  link or domain name and validates every card ([marketplace.md](marketplace.md)).
- `delegation.py`: `MarketplaceAgentExecutor`, the inbound executor that consults
  marketplace agents through `A2AClient`.
- `client/compat_v03.py`: **temporary** Travel Merchant compatibility code.
  It is not the recommended API and must be removed once a real remote SDK
  compatibility request passes.
- `lemonade_client.py`: local OpenAI-compatible Lemonade inference API.
- `demo/`: travel-provider action schema and operator/runbook code only.
- `registry.py`: existing *local feature registry*, not an agent registry.
  Do not overload it for remote agent discovery.

## Public usage

```python
from lemonade_a2a.client import A2AClient
from a2a.types import SendMessageRequest

# Construct request using upstream a2a-sdk protobuf types.
request = SendMessageRequest(...)
async with A2AClient("https://trusted-agent.example", streaming=False) as client:
    async for response in client.send_message(request):
        print(response)
```

For one text exchange there is a helper that builds the request, waits for a
running task to finish (polling with `GetTask`) and folds the events into an
`AgentReply` (`text`, `state`, `task_id`, `context_id`, `status_text`, `files`):

```python
async with A2AClient("https://trusted-agent.example") as client:
    reply = await client.ask("What is the forecast for Lisbon?")
    if reply.state == "TASK_STATE_INPUT_REQUIRED":  # the agent asks back
        reply = await client.ask("Saturday", context_id=reply.context_id, task_id=reply.task_id)
    print(reply.text, reply.files)
```

Failures raise the typed errors in `client/errors.py` (`AgentDiscoveryError`,
`UnsupportedProtocolError`, `RemoteAgentUnavailable`, `AgentAuthenticationError`,
`TaskExecutionError`). To find agents first, use a marketplace
([marketplace.md](marketplace.md)):

```python
from lemonade_a2a.discovery.marketplace import Marketplace

market = Marketplace("agents.example.com")  # link, domain or dns:<domain>
reply = await market.ask("weather", "Forecast for Lisbon?")
```

The official SDK's `ClientFactory` resolves the Agent Card and chooses an
advertised transport (JSON-RPC or HTTP+JSON, including its own 0.3 JSON-RPC
compatibility transport). `A2AClient(..., card=...)` skips the lookup for a card
that was already fetched and validated, as the marketplace does.
Never assume an MCP endpoint is A2A-compatible merely because it advertises
an A2A Card.

## Error, security and retry boundaries

- Fail closed on malformed URL schemes, untrusted private literal IPs and
  unsupported agent bindings. Beyond the literal-IP check at construction
  time, `client._ssrf_transport.SSRFGuardedTransport` resolves DNS exactly
  once per outbound request and connects to that specific, validated address
  (never a second, later DNS lookup inside the HTTP stack) — this closes the
  classic DNS-rebinding gap where a hostname resolves to a public address
  during validation and to a private/internal one at actual connect time.
  It covers both `client.sdk.A2AClient` (card resolution included, since the
  SDK's `ClientFactory` is handed the same guarded `httpx.AsyncClient`) and
  the temporary `client.compat_v03.RemoteA2AClient`. Every answer of the
  lookup is vetted; the transport connects to the first and tries the next one
  only if the connection cannot be made (`localhost` often resolves to `::1`
  first while a server listens on `127.0.0.1`). A host allowlist
  (`allowed_hosts`, or `LEMONADE_A2A_MARKETPLACE_ALLOWED_HOSTS` for the
  marketplace) is still the stronger option for untrusted, internet-sourced
  registry records.
- Set per-call timeouts; outbound HTTP redirects are disabled.
- Pass bearer tokens through headers; never include them in recorded Agent
  Cards, log events or query strings. Additional auth schemes belong in an
  SDK interceptor.
- Retry only idempotent reads with bounded exponential backoff on transient
  timeouts / 429 / 503. Never automatically retry `send_message`,
  `cancel_task`, checkout, or anything that could create side effects without
  a verified idempotency contract. (Moving to the next vetted address after a
  connection error is not a retry in this sense: nothing reached the server.)
- Retain task IDs, correlation IDs and trace context in future telemetry
  without logging prompts, secrets or full external response bodies.
- Close clients after use, particularly streamed calls; do not create a new
  TCP client per token.
- Support multi-provider discovery via implementations of `resolve(alias)`
  that return `AgentLocation`. Providers return locations; they must not
  duplicate protocol clients.

## Verification gates

1. Unit tests: discovery alias, URL validation, SDK delegation, cancellation,
   marketplace listing shapes, DNS SRV, `ask` polling and error mapping.
   Real-socket tests run two agents through a marketplace, including an
   `input-required` round trip and files in both directions
   (`tests/test_marketplace.py`, `tests/test_conversations_and_files.py`).
2. Contract: local SDK client -> Lemonade A2A server, including streaming.
3. Compatibility: external A2A 0.3 card -> SDK compat transport.
4. Integration: live Agent Bench search (read only), task result parsing.
5. Security: auth, HTTPS, redirects, SSRF, retries and trace redaction.
6. CI: coverage / Ruff / TCK / packaging. Keep the PR draft until green.

This is a staged API improvement, not a promise that all remote providers
already interoperate. A remote merchant's `action/input` payload is not an
A2A-standard action contract.
