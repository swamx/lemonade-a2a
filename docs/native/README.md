# Native Lemonade A2A: design and implementation plan

**Status: draft for maintainer review (2026-10-15). Design only: no native code exists and none is promised.**
Everything here is a proposal. Where it states a fact about upstream Lemonade it says whether it was
**verified** (read in the source at the pinned commit below) or **assumed** (inferred, needs checking).

This directory is the P3 deliverable of the [roadmap](../roadmap.md): how A2A becomes another protocol surface
inside `lemond`, and how to get there in reviewable steps. It extends [upstream-integration.md](../upstream-integration.md)
(target file layout, invariants, PR sequence), which stays the short summary and is not repeated here.

| Document | Answers |
|---|---|
| [architecture.md](architecture.md) | Where A2A sits in `lemond`, what the portable core is, what disappears from Python, threading |
| [mapping.md](mapping.md) | Routes, ports, authentication, configuration, CORS and headers |
| [state-and-lifecycle.md](state-and-lifecycle.md) | Task and artifact ownership, the streaming bridge, cancellation, limits |
| [capabilities-and-card.md](capabilities-and-card.md) | Agent Card from Lemonade's model state, skills policy, feature claims |
| [validation-and-migration.md](validation-and-migration.md) | Gates, how each suite runs against a native URL, rollout phases, exit criteria |
| [decisions.md](decisions.md) | The decision log, with recommendations and what needs a maintainer |
| [rfc-outline.md](rfc-outline.md) | Notes for the **human-written** upstream RFC |

## Upstream facts this plan is pinned to

- Repository: `lemonade-sdk/lemonade`, `main` at commit **`f0816fe46480067c156bb5b559f7ac6835bf61d4`** (read 2026-10-15).
- Upstream runs a **spec-driven process**: a new API surface needs an RFC in GitHub Discussions (category
  *Request for Comment (RFC)*) approved before a PR is opened, and its **AI content policy applies to RFCs and PR
  bodies, which must be human-written**. That is why this directory contains design documents and an outline,
  not RFC prose.
- Code owners per upstream's table: `@jeremyfowers` (new endpoints, new backends, breaking changes),
  `@Geramy` (networking and security), `@bitgamma` (the `lemonade` CLI). The RFC must tag them.
- Upstream has **no A2A code, issue or pull request** at that commit (the only related item is an unrelated
  request for an editor-oriented agent protocol).

## Glossary

| Term | Meaning here |
|---|---|
| Adapter | This repository's Python reference implementation (`lemonade-a2a`) |
| Native | A2A implemented in C++ inside `lemond` |
| Router | Lemonade's `Router` class: loads models, owns backend subprocesses, runs requests |
| Core | The protocol behavior that must be identical in both implementations (see [architecture.md](architecture.md)) |
| Registry | `src/lemonade_a2a/spec/features.json`, the list of features with evidence |

## Decided versus open

Decided in this repository (and enforced by the adapter's tests): the behavior table in
[protocol-mapping.md](../protocol-mapping.md), the exposure and limit semantics, the telemetry instrument names.
Open and needing upstream input: see [decisions.md](decisions.md). Nothing here changes the Python adapter.
