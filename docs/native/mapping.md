# Endpoint, port, authentication and configuration mapping

**Status: draft (2026-10-15).** *Verified* = read at upstream commit `f0816fe`; *assumed* = needs checking.

## Port and listener (decision D1)

**Recommendation: share Lemonade's primary HTTP port; do not add a second listener.**

| Option | For | Against |
|---|---|---|
| Shared port (recommended) | One port, one API key, one TLS/proxy story; matches `/mcp` (MCP) and `/v1/messages` (Anthropic) precedents; "many clients, one server" invariant | A2A paths must be added to the auth and routing special cases (below) |
| Separate listener | Isolation of exposure | A second port to secure and document; the adapter's own history shows port clashes (Lemonade's WebSocket port) are a real cost; more configuration |

*Verified:* the HTTP port defaults to 13305 (`config.json` key `port`), and the WebSocket port is `"auto"` (the
adapter uses 9100 only because a stock install may hold 9000).

## Routes

| Purpose | Path | Notes |
|---|---|---|
| Agent Card | `GET /.well-known/agent-card.json` | Path is fixed by the A2A spec, like `/mcp` is fixed by the MCP spec: an **explicit exception** to upstream's four-prefix rule (`/api/v0`, `/api/v1`, `/v0`, `/v1`) |
| JSON-RPC binding | `POST /a2a/jsonrpc` | Matches the adapter's legacy path; the Card advertises the exact URL, so clients follow it |
| HTTP+JSON binding | `/a2a/rest/...` (`message:send`, `message:stream`, `tasks`, `tasks/{id}`, ...) | Same shape as the adapter's legacy REST prefix; arrives after the core (rollout step 3) |

Two routing hazards, both *verified* in `server.cpp`:

1. **SPA fallback.** The web app registers a catch-all
   `GET ^(?!/api|/v0|/v1|/static|/live|/status|/internal|/docs(/|$)).*`. `/.well-known/...` and `/a2a/...` are not
   in that exclusion list. httplib matches in registration order, and the MCP module is registered *before* the
   static/web-app routes, so registering A2A beside it works; but the dependence on order must be pinned by a test
   (the adapter had exactly this class of regression: a catch-all tenant route shadowed other routes and the TCK
   caught it). Prefer also adding `/.well-known` and `/a2a` to the exclusion pattern.
2. **Prefix invariant.** Registering through the `register_get`/`register_post` lambdas would create four
   prefixes per route. A2A paths are registered directly, like MCP, and the exception is documented in
   `AGENTS.md` and the API docs.

## Authentication (decision D2)

*Verified:* `Server::authenticate_request` gates a path only when it is in an explicit allowlist:
`/api/`, `/v0/`, `/v1/` prefixes and the literal `/mcp`; `/internal/*` uses a separate admin key; `/metrics` is its
own case. **A new A2A path that is not added there would be unauthenticated**, so adding the A2A prefix to
`is_api_route` is part of the first change, with a test that `LEMONADE_API_KEY` set means 401 without a key.

| Concern | Adapter today | Native proposal |
|---|---|---|
| API key | `LEMONADE_A2A_API_KEY`, bearer or `X-API-Key` | Lemonade's `LEMONADE_API_KEY` (bearer). Same invariant, one key |
| Agent Card | Public, declares the bearer scheme only when auth is enforced | Public for discovery (spec intent). **Needs `@Geramy`'s review** |
| Per-user task isolation | Named keys; key name is the task owner | **Gap:** upstream has a single key (plus an admin key), so every caller is one identity. Options: (a) accept a single tenant and document it, (b) derive an owner from a client session header (*assumed*: upstream honors `X-Client-Session-Id` for telemetry; unverified for this use). Isolation is a P4 concern unless upstream grows named keys |
| Rate limiting | `LEMONADE_A2A_RATE_LIMIT_PER_MINUTE` | **Gap:** no upstream rate limiter was found (*assumed*); out of scope, a proxy concern |
| TLS / mTLS | Adapter terminates TLS | *Assumed* not provided by `lemond`; terminate at a reverse proxy. Exposure profiles are replaced by Lemonade's own host binding plus the API key |

## Configuration (decision D4)

*Verified:* settings live in a sparse `config.json`, validated in `runtime_config.cpp`, editable with
`lemonade config`; `lemond` itself takes only `[cache_dir] [config_dir] --port --host`. So the native feature
switch is a config key, not a flag; an ephemeral `--a2a` override can follow the `--port` precedent if the CLI
owner (`@bitgamma`) wants it. **The name is the maintainers' to choose**; `a2a.*` below is a working name.

| Adapter setting | Native |
|---|---|
| `LEMONADE_A2A_HOST`, `_PORT` | Not applicable (shared listener) |
| `LEMONADE_A2A_PUBLIC_URL` | `a2a.public_url`; default derived from the request `Host` so a card works behind localhost |
| `LEMONADE_A2A_AGENT_NAME`, `_DESCRIPTION` | `a2a.agent_name`, `a2a.agent_description` |
| `LEMONADE_BASE_URL`, `LEMONADE_API_KEY`, `LEMONADE_TIMEOUT_SECONDS` | Not applicable (in-process) |
| `LEMONADE_MODEL` | Not a fixed setting: a model is resolved per request (see [capabilities-and-card.md](capabilities-and-card.md)); optional `a2a.default_model` |
| `LEMONADE_A2A_MAX_INPUT_CHARS`, `_MAX_INPUT_PARTS` | `a2a.max_input_chars`, `a2a.max_input_parts` |
| `LEMONADE_A2A_MAX_TASK_SECONDS`, `_MAX_CONCURRENT_TASKS`, `_MAX_STORED_TASKS` | `a2a.max_task_seconds`, `a2a.max_concurrent_tasks`, `a2a.max_stored_tasks` |
| `LEMONADE_A2A_REASONING`, `_CANCEL_ON_DISCONNECT` | `a2a.reasoning`, `a2a.cancel_on_disconnect` |
| `LEMONADE_A2A_MAX_REQUEST_BYTES` | Whatever upstream's HTTP layer already enforces (*assumed*: check) |
| `LEMONADE_A2A_PROFILE`, `_API_KEY(S)`, `_RATE_LIMIT_*`, `_SSL_*` | Not applicable, see Authentication |
| `LEMONADE_A2A_OTEL*`, `OTEL_*` | Not applicable: use upstream's own telemetry (see [validation-and-migration.md](validation-and-migration.md)) |
| `LEMONADE_A2A_COMPAT`, `_FEATURES`, TOML layers | Not applicable; feature claims are static per build |

Defaults carry over unchanged (100,000 characters, 32 parts, 600 s, 8 tasks, 1,000 stored tasks) so behavior
matches the adapter's tests. `a2a.enabled` defaults to **false**.

## CORS and headers

*Verified:* upstream hard-codes its CORS allow-headers. A2A needs `A2A-Version` (and `A2A-Extensions` if
extensions are ever required) added, otherwise browser clients cannot call it. The `A2A-Version` check (unsupported
version returns the version-not-supported error) and the content-type rules (`-32005` for JSON-RPC, 415 for
HTTP+JSON) are part of the core.
