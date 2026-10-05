# Security Model

Lemonade A2A turns a local inference service into an agent-facing protocol endpoint. That boundary must be treated as security-sensitive.

## Trust boundaries

```text
UNTRUSTED                         TRUSTED LOCAL BOUNDARY
A2A peer ──► A2A adapter ─────────────► Lemonade server
                 │
                 └── validation / policy / limits
```

Agent Cards, messages, metadata, URLs, files, structured parts and artifacts received from another agent are untrusted.

## Default deployment

The safest default is:

- A2A adapter bound to loopback;
- Lemonade bound to loopback;
- no remote file fetching;
- no shell/tool execution;
- bounded message/task sizes;
- explicit opt-in before LAN/public exposure.

## Implemented controls

| Control | Status |
|---|---|
| Exposure profiles (`LEMONADE_A2A_PROFILE` = `local` / `lan` / `external`): the adapter **refuses to start** when the bind address, authentication, TLS and rate limit do not fit the profile (see [Exposure profiles](#exposure-profiles)) | Implemented |
| Non-text parts rejected; no URL/file fetching | Implemented |
| Input limits: characters (`LEMONADE_A2A_MAX_INPUT_CHARS`) and parts (`LEMONADE_A2A_MAX_INPUT_PARTS`) | Implemented |
| Backend timeout (`LEMONADE_TIMEOUT_SECONDS`) and per-task deadline (`LEMONADE_A2A_MAX_TASK_SECONDS`) | Implemented |
| Concurrency cap (`LEMONADE_A2A_MAX_CONCURRENT_TASKS`); excess tasks are `REJECTED` | Implemented |
| Bounded task store (`LEMONADE_A2A_MAX_STORED_TASKS`): oldest *finished* tasks are evicted, live tasks never | Implemented |
| Backend errors reduced to short client-safe messages | Implemented |
| API-key auth: one shared key (`LEMONADE_A2A_API_KEY`) or several named keys (`LEMONADE_A2A_API_KEYS=alice:key1,bob:key2`); `Authorization: Bearer` or `X-API-Key`, constant-time compare against every key, 401 + `WWW-Authenticate`; declared as a `bearer` scheme on the Agent Card only when enabled; Agent Card and `/healthz` stay public | Implemented |
| Per-user task isolation: the key's name is the task owner, so one caller cannot read, list, continue or cancel another caller's tasks (they get the same *task not found* as for a task that never existed) | Implemented (tested on both bindings) |
| Rate limiting (`LEMONADE_A2A_RATE_LIMIT_PER_MINUTE`): token bucket per identity, HTTP 429 with `Retry-After`; discovery and `/healthz` are exempt; unauthenticated requests are rejected before they can spend anyone's budget; tracked identities are capped | Implemented |
| Mutual TLS (`LEMONADE_A2A_SSL_REQUIRE_CLIENT_CERT=1` with `LEMONADE_A2A_SSL_CA_CERTS`): the handshake fails without a client certificate from that CA | Implemented (tested with generated certificates: valid, missing and foreign-CA clients) |
| Key forwarded to a protected Lemonade backend (`LEMONADE_API_KEY`) | Implemented |
| TLS in-process (`LEMONADE_A2A_SSL_CERTFILE` / `LEMONADE_A2A_SSL_KEYFILE`) or at a reverse proxy | Implemented (uvicorn); certificates are the operator's responsibility |
| Non-JSON request bodies rejected (`-32005` / HTTP 415) | Implemented |
| Request body size limit (`LEMONADE_A2A_MAX_REQUEST_BYTES`, default 1 MiB): HTTP 413 from `Content-Length` and for chunked streams, before the body is buffered or authenticated | Implemented |
| No interactive docs or OpenAPI pages (`/docs`, `/redoc`, `/openapi.json`) | Implemented |
| Response headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` (Agent Card stays cacheable) | Implemented |
| Startup warnings for allowed-but-weak combinations (a non-local profile without TLS; `lan` without a rate limit) | Implemented |
| Request bodies that are not valid UTF-8 answered as a parse error (`-32700` / HTTP 400) instead of an internal error (found by fuzzing) | Implemented |
| Backend stream events of the wrong shape become a failed task with a generic message, not a crash (found by fuzzing) | Implemented |
| A stream that fails or hits its deadline part-way closes its artifact (`last_chunk=true`) before the task turns `FAILED` | Implemented |
| Graceful shutdown cancels in-flight inference | Implemented |
| Slow-consumer backpressure | Provided by the A2A SDK's bounded event queue (1,024 events) and **verified end to end** (`tests/test_live_lifecycle.py`): a client that connects and never reads stalls only its own stream, the adapter keeps serving others, memory stays flat, and the task ends `FAILED` at its deadline. Resubscribers that fall behind are dropped by the SDK. The bound is the SDK's and is not configurable here |
| Opt-in cancel on disconnect (`LEMONADE_A2A_CANCEL_ON_DISCONNECT=1`) | Implemented; default off (see [protocol-mapping.md](protocol-mapping.md)) |
| Fuzzing of messages, parts, metadata, queries, raw bytes and backend stream lines | Implemented (`tests/test_fuzz.py`, property-based) |
| OAuth 2.0 / OpenID Connect token validation | **Not implemented, by decision**: terminate OIDC at a gateway (see [Identity](#identity-and-oauth)) |
| Prompt/content logging | Not performed; failure logs contain exception summaries only |

## Exposure profiles

`LEMONADE_A2A_PROFILE` states how the adapter is exposed, and the configuration is checked at startup. A combination that does not fit the profile stops the adapter with a message naming the missing setting, rather than only logging a warning.

| Profile | For | Requirements | Typical settings |
|---|---|---|---|
| `local` (default) | One user, one machine, client on the same host | Listens on loopback only (`127.0.0.1`, `localhost`, `::1`) | none |
| `lan` | A trusted network (home, lab, office) | Authentication: `LEMONADE_A2A_API_KEY`, `LEMONADE_A2A_API_KEYS` or client certificates. TLS and a rate limit are strongly recommended and produce a startup warning when absent | `LEMONADE_A2A_HOST=0.0.0.0`, `LEMONADE_A2A_API_KEYS=...`, `LEMONADE_A2A_RATE_LIMIT_PER_MINUTE=120` |
| `external` | Reachable from the internet, normally behind a reverse proxy | Authentication **and** TLS (`LEMONADE_A2A_SSL_CERTFILE`/`KEYFILE`) **and** a rate limit | all of the above plus TLS files; mTLS if clients can hold certificates |

Behind a TLS-terminating proxy, run `lan` or `external` with the proxy in front and keep the adapter's own listener on a private interface; set `LEMONADE_A2A_PUBLIC_URL` to the proxy's URL. The profile does not change what the adapter does for each request; it only decides which combinations are allowed to start.

## Identity and OAuth

Each named API key is an identity. The identity becomes the task owner in the A2A SDK's task store, so isolation between callers needs no extra code: `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask` and a follow-up message that names a task all treat another caller's task as not found. A single shared key (`LEMONADE_A2A_API_KEY`) is one identity named `default`.

Mutual TLS controls who may connect; it does not name the caller (the ASGI server does not pass the peer certificate to the app), so every certificate holder is the same identity unless API keys are used as well. The two combine: certificates for transport access, keys for who is calling.

**OAuth 2.0 / OpenID Connect is deliberately not implemented in the adapter.** Validating tokens needs a JWKS client, issuer/audience/clock-skew policy and key rotation, which are better done once, in a gateway, than per service. The supported pattern is: an OIDC-aware gateway or reverse proxy (oauth2-proxy, Envoy, nginx with `auth_request`, a cloud API gateway) authenticates the caller and forwards to the adapter over a private link that requires a named API key (or mTLS) for the gateway. Per-user identity from the token is then the gateway's concern. Revisit if a deployment needs the adapter itself to see the token subject (for per-user task ownership behind a shared gateway).

## Threats

### Prompt injection

A2A interoperability does not make remote content trustworthy. Prompt injection can arrive through text, files, artifacts or delegated-agent responses. The adapter must not convert model text into privileged actions by itself.

### SSRF and file access

Remote URLs and file references are never fetched: file, URL and data parts are rejected on both bindings (tested with `file:`, cloud-metadata and path-traversal values). Fetching, and push notifications (which call back a caller-supplied URL), stay off until they pass through the policy in `src/lemonade_a2a/safe_urls.py`, which is tested ahead of time: HTTPS only by default; no credentials in the URL; only real DNS names or IP literals (so `2130706433` and `0x7f.1` cannot be smuggled to the resolver); no internal suffixes (`.local`, `.internal`, ...); **every** resolved address must be globally routable (loopback, RFC 1918, link-local including `169.254.169.254`, CGNAT, multicast and IPv4-mapped IPv6 are refused); and the caller must connect to the vetted address instead of resolving again (DNS rebinding). Size limits, redirect handling and timeouts are still required when a fetcher is added.

### Resource exhaustion

Enforce limits for:

- message size;
- number and size of parts;
- concurrent tasks;
- task lifetime;
- streaming buffers;
- backend timeout;
- stored task history.

### Information disclosure

Do not expose environment variables, local paths, backend stack traces, internal hostnames, API keys or raw exceptions through A2A errors.

### Agent Card spoofing

Discovery metadata describes capability; it does not establish identity or trust. Consumers should authenticate peers according to their deployment environment.

### Model capability confusion

Do not advertise vision, tool execution, filesystem access, or other capabilities unless they are explicitly configured and supported end to end.

## Privacy modes (future)

A future policy layer may define:

- `local`: loopback only;
- `lan`: trusted private network peers;
- `remote`: authenticated remote A2A peers.

These are deployment policies, not modifications to the A2A protocol.

## Logging

Default logs should contain operational metadata, not prompts or generated content. Content logging must be explicit opt-in.

## Automated security checks

Every pull request runs CodeQL (Python, JavaScript, Actions), bandit, pip-audit, detect-secrets against `.secrets.baseline`, and GitHub dependency review; the same scans run weekly. Findings reviewed as false positives are recorded in the baseline. See [governance.md](governance.md) for how these gate merges.

## Reporting

Until a formal security policy is established, vulnerabilities should be reported privately to the repository owner rather than opened with exploit details in a public issue.
