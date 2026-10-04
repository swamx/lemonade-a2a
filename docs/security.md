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
| Binds to loopback by default (`LEMONADE_A2A_HOST=127.0.0.1`); warns at startup when bound elsewhere without an API key | Implemented |
| Non-text parts rejected; no URL/file fetching | Implemented |
| Input limits: characters (`LEMONADE_A2A_MAX_INPUT_CHARS`) and parts (`LEMONADE_A2A_MAX_INPUT_PARTS`) | Implemented |
| Backend timeout (`LEMONADE_TIMEOUT_SECONDS`) and per-task deadline (`LEMONADE_A2A_MAX_TASK_SECONDS`) | Implemented |
| Concurrency cap (`LEMONADE_A2A_MAX_CONCURRENT_TASKS`); excess tasks are `REJECTED` | Implemented |
| Bounded task store (`LEMONADE_A2A_MAX_STORED_TASKS`): oldest *finished* tasks are evicted, live tasks never | Implemented |
| Backend errors reduced to short client-safe messages | Implemented |
| Optional API-key auth (`LEMONADE_A2A_API_KEY`): `Authorization: Bearer` or `X-API-Key`, constant-time compare, 401 + `WWW-Authenticate`; declared as a `bearer` scheme on the Agent Card only when enabled; Agent Card and `/healthz` stay public | Implemented |
| Key forwarded to a protected Lemonade backend (`LEMONADE_API_KEY`) | Implemented |
| TLS in-process (`LEMONADE_A2A_SSL_CERTFILE` / `LEMONADE_A2A_SSL_KEYFILE`) or at a reverse proxy | Implemented (uvicorn); certificates are the operator's responsibility |
| Non-JSON request bodies rejected (`-32005` / HTTP 415) | Implemented |
| Request body size limit (`LEMONADE_A2A_MAX_REQUEST_BYTES`, default 1 MiB): HTTP 413 from `Content-Length` and for chunked streams, before the body is buffered or authenticated | Implemented |
| No interactive docs or OpenAPI pages (`/docs`, `/redoc`, `/openapi.json`) | Implemented |
| Response headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` (Agent Card stays cacheable) | Implemented |
| Startup warnings when bound beyond loopback without an API key or without TLS | Implemented |
| Graceful shutdown cancels in-flight inference | Implemented |
| Streaming buffer bounds / slow-consumer backpressure | Not implemented (relies on the SDK queue and Lemonade's own generation rate) |
| Per-client rate limiting, multiple keys/identities, OAuth/OIDC, mTLS | Not implemented |
| Prompt/content logging | Not performed; failure logs contain exception summaries only |

A single shared API key authenticates *the caller to the adapter*; it does not give per-user task isolation (the task store owner is not derived from the key).

## Threats

### Prompt injection

A2A interoperability does not make remote content trustworthy. Prompt injection can arrive through text, files, artifacts or delegated-agent responses. The adapter must not convert model text into privileged actions by itself.

### SSRF and file access

Remote URLs and file references must never be fetched automatically by the MVP. Future fetch support requires allowlists, scheme validation, size limits, redirect policy and private-network protections.

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
