# RFC skeleton (tables and facts filled in; prose left to the author)

**This is not RFC text and must not be pasted as one.** Upstream's AI content policy (Rule 1, read at commit
`f0816fe`) says AI-generated *prose* is not permitted in any Issue, Discussion or PR body, and that anyone may ask
for it to be replaced and expect no further replies until it is. The policy's own note says tables, diagrams, code
and graphics are *not* prose. So this skeleton holds the facts, tables and evidence links; every `WRITE:` slot is
a paragraph that has to be written by a human, in their own words. Nothing here has been posted anywhere.

Follow upstream's RFC template (category *Request for Comment (RFC)*) and its spec-writing guide: first sentence
of a section states its scope; tables over prose; precise names; each fact stated once; claims checked against
the source, with unverified ones marked.

## 1. User Story

WRITE: who needs to let another agent or framework call a local model through an open standard, what they do
today, and what native support gives them. Say plainly that no user demand has been collected yet and ask for it.

| Fact | Source |
|---|---|
| A2A is an open agent-to-agent protocol (Linux Foundation project) with official SDKs in several languages | the A2A project |
| A working reference sidecar exists and passes the official conformance kit | [conformance.md](../conformance.md) |
| Upstream has no A2A code, issue or PR at `f0816fe` | [README.md](README.md#upstream-facts-this-plan-is-pinned-to) |

## 2. High-Level Design

WRITE: what changes from a user's and developer's point of view, without citing code (the template says so).

| Aspect | Proposal | Detail |
|---|---|---|
| Surface | One more protocol surface served by `lemond`; translation only, no new inference path | [architecture.md](architecture.md) |
| Enablement | Opt-in, off by default | [mapping.md](mapping.md#configuration-decision-d4) |
| Discovery | Agent Card at the spec-fixed well-known path | [capabilities-and-card.md](capabilities-and-card.md) |
| Bindings | JSON-RPC first; HTTP+JSON after the core is stable | [validation-and-migration.md](validation-and-migration.md#rollout-phases) |
| Streaming and cancellation | Streamed tokens; cancel stops the real generation | [state-and-lifecycle.md](state-and-lifecycle.md) |
| Authentication | Existing API key; no new auth system | [mapping.md](mapping.md#authentication-decision-d2) |

## 3. Breaking Changes

WRITE: none intended. Say what is added (new paths, one config key) and that existing endpoints are untouched.

## 4. Maintenance Plan

WRITE: who maintains it, how protocol-version churn is handled, what a human tester must do.

| Question from the template | Answer drawn from the evidence |
|---|---|
| Fully maintained by CI? | The shared black-box suites run against a URL: official conformance kit, independent client SDKs, contract vectors, cancellation proof ([validation-and-migration.md](validation-and-migration.md)) |
| Human testers needed? | Once, for a pass on real hardware not available to the author (see the hardware items in [roadmap.md](../roadmap.md)) |
| How is the change protected? | The behavior table in [protocol-mapping.md](../protocol-mapping.md) ported as upstream-convention tests |

## 5. Risks

WRITE: your honest assessment. The facts to base it on:

| Risk | Fact |
|---|---|
| Scope | Upstream's own policy calls an agentic feature a scope increase |
| Security | A path missing from the auth allowlist is unauthenticated (verified in `authenticate_request`) |
| Maintenance | A second protocol parser, mitigated by the shared suites |
| Isolation | Upstream has a single API key, so there is no per-user task isolation ([decisions.md](decisions.md), D8) |

## Evidence (all reproducible from this repository)

| Claim | Source |
|---|---|
| Official conformance kit: 161 passed, 0 failed with the one-line messageId workaround (157 passed, 0 failed, 4 expected-fail without) | [conformance.md](../conformance.md) |
| Independent client SDKs (CLI, JavaScript, Go, .NET, Java) interoperate over both bindings | [interoperability.md](../interoperability.md) |
| Validated on a real Lemonade across three llama.cpp backends | [real-lemonade-validation.md](../real-lemonade-validation.md) |
| About 5-15 ms added time-to-first-token; flat throughput under load | [benchmarks.md](../benchmarks.md) |
| Cancellation stops real generation (177 ms versus 30.9 s queued) | [benchmarks.md](../benchmarks.md) |
| Fuzzing found and fixed two defects | [roadmap.md](../roadmap.md) |

## Questions for the maintainers (decisions only they can make)

See [decisions.md](decisions.md#questions-only-upstream-maintainers-can-answer).

## Process

| Step | Detail |
|---|---|
| Post | Discussions, category *Request for Comment (RFC)*, written by the author |
| Tag | Code owners for new endpoints, networking and security, and the CLI (upstream's table) |
| Raise | The project's developer chat channel, so other contributors see it |
| Maintain | Keep an "Update N" list at the top as the thread moves |
| Gate | No PR until the RFC carries the `rfc:on-roadmap` label |
