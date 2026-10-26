# Decision log

**Status: draft (2026-10-15).** Each decision has a recommendation, the reason, and who must weigh in. "Owner" is
the upstream code owner for the area from upstream's table (`@jeremyfowers` new endpoints, `@Geramy` networking
and security, `@bitgamma` CLI); this repository can recommend but cannot decide.

| Id | Question | Recommendation | Why | Needs |
|---|---|---|---|---|
| D1 | Shared port or separate listener? | Shared HTTP port, paths `/.well-known/agent-card.json` and `/a2a/...` as documented exceptions | Precedent (MCP, Anthropic); one key, one proxy story; see [mapping.md](mapping.md) | `@jeremyfowers` |
| D2 | Authentication of A2A routes and the Agent Card | Add the A2A prefix to the `is_api_route` allowlist; card public | An un-listed path is unauthenticated (verified); discovery needs the card without a key | `@Geramy` |
| D3 | Task state: job system or A2A-owned store? | A2A-owned bounded in-memory store behind an interface | Job engine is a single-worker, client-recipe, non-streaming design (verified) | `@jeremyfowers` (informational) |
| D4 | Enablement and configuration naming | `a2a.*` keys in `config.json`, `a2a.enabled` default false; optional ephemeral flag | `lemond` flags are only `--port --host`; settings live in config (verified). Names are placeholders | `@bitgamma` |
| D5 | Agent Card generated per request or cached? | Cached with invalidation on model-type change | Matches the adapter's cache headers; cheap | implementation detail |
| D6 | Where do the telemetry instruments live? | Same instrument names through upstream's telemetry layer | Dashboards and alerts keep working; generation spans are already upstream's | upstream telemetry owner (unknown) |
| D7 | Where does the feature registry live and how does it distinguish implementations? | Stays in this repository; add an `implementation` dimension and "not applicable" entries | Keeps evidence next to the suites that produce it | this repository |
| D8 | Per-user task isolation with a single upstream key | Accept one tenant; document; revisit only if upstream adds named keys | Do not invent a second auth system | `@Geramy` |
| D9 | Reasoning content default | `drop`, opt-in separate artifact | Parity with the adapter, which is tested and benchmarked | none |

## Spikes (answer before phase 1; each is small and read-only or on a scratch branch)

| Id | Question | Done when |
|---|---|---|
| S1 | Can the bridge consume `Router::chat_completion_stream` through an in-process sink and take a task-owned cancel flag, or is a Router callback needed? | A 50-line scratch branch streams a canned prompt into a string and cancels on a flag |
| S2 | How are the web-app catch-all, `/.well-known` and A2A routes ordered, and does a test pin it? | **Read 2026-10-15 (verified at `f0816fe`):** MCP is registered at `server.cpp` line 1550, the static and web-app routes after it (1552 onward), and the SPA fallback regex does not exclude `/.well-known` or `/a2a`. Registering A2A beside MCP therefore wins by order. **Still open:** a test that pins the order and the 401-with-key behavior, which needs a build |
| S3 | How does the TCK get deterministic scenario answers from `lemond`? | A recipe (mock model or recorded prompts) runs the TCK clean for the declared capabilities |
| S4 | How are upstream telemetry instruments registered, and can new ones be added without a new dependency? | One counter appears in `/metrics` from scratch code |
| S5 | What body-size limit and TLS support does `lemond` actually have? | **Done (verified at `f0816fe`):** no explicit payload limit and no TLS termination in `server.cpp`; see [mapping.md](mapping.md) |

## Questions only upstream maintainers can answer

1. Is an agent-interoperability protocol in scope for `lemond`, and under which working group, if any?
2. Is a config key (`a2a.*`) acceptable, or should A2A be a separate optional component?
3. Is a delta-callback on the Router acceptable if the sink approach is not enough?
4. Should the A2A paths take the quad-prefix, in addition to the spec-fixed ones?
5. Is the compatibility registry welcome upstream, or does it stay external?
