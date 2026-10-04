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
| Binds to loopback by default (`LEMONADE_A2A_HOST=127.0.0.1`) | Implemented |
| Non-text parts rejected; no URL/file fetching | Implemented |
| Input size limit (`LEMONADE_A2A_MAX_INPUT_CHARS`) | Implemented |
| Backend timeout (`LEMONADE_TIMEOUT_SECONDS`) | Implemented |
| Backend errors reduced to short client-safe messages | Implemented |
| Authentication / TLS | Not implemented; terminate TLS and authenticate in front of the adapter before any non-loopback use |
| Limits on concurrent tasks, task lifetime, stored history, streaming buffers | Not implemented (in-memory task store is unbounded) |
| Prompt/content logging | Not performed; failure logs contain exception summaries only |

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

## Reporting

Until a formal security policy is established, vulnerabilities should be reported privately to the repository owner rather than opened with exploit details in a public issue.
