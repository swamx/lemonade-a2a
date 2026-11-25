# Security Model

Lemonade A2A turns a local inference service into an agent-facing protocol endpoint. That boundary must be treated as security-sensitive.

## Trust boundaries

```text
UNTRUSTED                         TRUSTED LOCAL BOUNDARY
A2A peer ──► A2A adapter ─────────────► Lemonade server
                 │
                 ├── validation / policy / limits
                 │
                 └──► (opt-in) marketplace listings, DNS answers,
                      remote agents and their replies      UNTRUSTED
```

Agent Cards, messages, metadata, URLs, files, structured parts and artifacts received from another agent are untrusted. With a marketplace configured, the adapter also *calls out*: marketplace listings, DNS SRV/TXT answers, the cards they point to and every remote reply are untrusted in the same way.

## Default deployment

The safest default is:

- A2A adapter bound to loopback;
- Lemonade bound to loopback;
- no remote file fetching;
- no outbound calls (no marketplace configured) and no file parts;
- no shell/tool execution;
- bounded message/task sizes;
- explicit opt-in before LAN/public exposure.

## Implemented controls

| Control | Status |
|---|---|
| Exposure profiles (`LEMONADE_A2A_PROFILE` = `local` / `lan` / `external`): the adapter **refuses to start** when the bind address, authentication, TLS and rate limit do not fit the profile (see [Exposure profiles](#exposure-profiles)) | Implemented |
| Non-text parts rejected by default; opt-in text files, data parts and images (`LEMONADE_A2A_FILES`, size-limited, fenced as data); no URL/file fetching | Implemented |
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
| OpenTelemetry privacy modes (`LEMONADE_A2A_OTEL_CAPTURE` `none` / `metadata` / `content`): off by default; `none` and `metadata` export nothing derived from prompts (property-tested); the `external` profile refuses `content` and plaintext remote OTLP | Implemented ([observability.md](observability.md)) |
| `/metrics` (`LEMONADE_A2A_OTEL_PROMETHEUS`) and the capabilities endpoint (`LEMONADE_A2A_EXPOSE_CAPABILITIES`): both **off by default** and **authenticated** like every non-discovery route (version and configuration disclosure helps attackers) | Implemented |
| Persistent tasks (`LEMONADE_A2A_TASK_STORE=sqlite`): the task database is created owner-only (0600) on POSIX; **it contains prompts and answers**, so protect and rotate it like any data store; owners stay separate; the oldest finished tasks are deleted past `LEMONADE_A2A_MAX_STORED_TASKS` | Implemented |
| Plugins (`lemonade_a2a.*` entry points): a plugin runs with the adapter's privileges and must be trusted like any dependency; an authenticator that raises denies the request; a plugin built for another extension API version is refused | Implemented ([extending.md](extending.md)) |
| Outbound requests (marketplace listings, cards, remote agents): one guarded transport that resolves DNS once, refuses an answer set containing any private, loopback, link-local or metadata address, connects only to the vetted addresses (the next one only if a connection is refused) and never follows redirects; listings at most 4 MiB and 200 agents, cards at most 1 MiB; `LEMONADE_A2A_MARKETPLACE_ALLOW_LOCAL` (development, LAN) is refused under `external`; optional host allowlist (`LEMONADE_A2A_MARKETPLACE_ALLOWED_HOSTS`) | Implemented ([marketplace.md](marketplace.md)) |
| DNS SRV/TXT discovery: targets pass the same URL policy as typed links; TXT paths must be plain absolute paths; the marketplace key is never sent to a host named only by DNS | Implemented |
| Credentials across agents: the marketplace key is sent only to the marketplace hosts the operator named; inbound API keys are never forwarded to remote agents | Implemented |
| OpenAI-compatible chat surface (`/v1/models`, `/v1/chat/completions`, on by default, `LEMONADE_A2A_OPENAI_API=0` turns it off): the same middleware as the A2A routes (authentication, rate limit, body-size limit), the same executor bounds (deadline, concurrency cap), inline images only (remote image URLs are refused, never fetched), sanitized errors. Registering an agent in Lemonade's UI gives Lemonade an API key of the agent's | Implemented ([lemonade-ui.md](lemonade-ui.md)) |
| Conversation memory: keyed by caller identity and `contextId` (another caller reusing the id sees nothing), bounded, idle TTL, memory only; `LEMONADE_A2A_CONVERSATION_TURNS=0` disables it | Implemented |
| Compatibility gate (`LEMONADE_A2A_COMPAT=strict`): refuses to start on an untested or broken component combination | Implemented ([upgrading.md](upgrading.md)) |
| `support-bundle`: secrets and the home directory are redacted before anything is written; read it before sharing | Implemented |

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

Delegation is the one place where model output drives an action: the local model's plan decides which listed agent receives which text. The action is bounded. It can only send a text message (plus the caller's own forwarded parts) to an agent on the configured marketplace, at most `LEMONADE_A2A_MAX_DELEGATIONS` times per task, through the guarded transport, and never with the caller's credentials. Agent descriptions, remote replies and file contents are length-bounded and enclosed in `<agent_reply>` / `<file>` fences that the content cannot close, and the prompts say they are data. That reduces, but does not remove, the chance that a hostile listing or reply steers the answer. Use marketplaces you trust and pin them with the host allowlist.

### SSRF and file access

Remote URLs and file references are never fetched. By default file, URL and data parts are rejected on both bindings (tested with `file:`, cloud-metadata and path-traversal values). With `LEMONADE_A2A_FILES=1`, inline text files (listed text media types, valid UTF-8, at most `LEMONADE_A2A_MAX_FILE_BYTES`) and data parts are given to the model inside a `<file>` fence that the content cannot close, images are passed only with `LEMONADE_A2A_FILE_IMAGES=1`, and a URL part reaches the model as a reference string only (it is forwarded unchanged to marketplace agents, which fetch it themselves, see [marketplace.md](marketplace.md)); the Agent Card lists exactly the accepted media types and the SDK refuses others. Fetching, and push notifications (which call back a caller-supplied URL), stay off until they pass through the policy in `src/lemonade_a2a/safe_urls.py`, which is tested ahead of time: HTTPS only by default; no credentials in the URL; only real DNS names or IP literals (so `2130706433` and `0x7f.1` cannot be smuggled to the resolver); no internal suffixes (`.local`, `.internal`, ...); **every** resolved address must be globally routable (loopback, RFC 1918, link-local including `169.254.169.254`, CGNAT, multicast and IPv4-mapped IPv6 are refused); and the caller must connect to the vetted address instead of resolving again (DNS rebinding). Size limits, redirect handling and timeouts are still required when a fetcher is added.

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

With delegation, the caller's request (or the model's rewording of it) and any forwarded file parts leave the machine for the chosen remote agents. That is the purpose of the feature, but it changes the locality guarantee: leave `LEMONADE_A2A_MARKETPLACE` unset where requests must stay local. Remote failures reach the caller as a short note naming the agent, not as the remote error text.

### Agent Card spoofing

Discovery metadata describes capability; it does not establish identity or trust. Consumers should authenticate peers according to their deployment environment.

Marketplace cards are not signature-checked yet, and plain DNS answers can be spoofed (no DNSSEC validation). A listing can therefore present any name and description; the protections are the endpoint policy above (a card cannot point at a private address), HTTPS to the advertised host, and the optional host allowlist.

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
