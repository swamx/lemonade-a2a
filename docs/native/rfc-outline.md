# RFC outline (notes for the author)

**These are notes, not RFC text.** Lemonade's AI content policy requires RFCs and PR bodies to be written by a
human, and its important decisions to be made "through human-to-human discourse". Use this outline and the linked
evidence; write the prose yourself, in your own words, and follow upstream's RFC template and spec-writing guide.
Nothing here has been posted anywhere.

## Sections upstream's template asks for, with the material to draw on

1. **User story**
   - People need to let other agents and agent frameworks call a local model through an open standard.
   - Today that needs a separate sidecar; the benefit of native is one server, one key, no second process.
   - Evidence the need is real: none from users yet. Say so, and ask for it in the thread.
2. **High-level design** (no code citations here, per the template)
   - A2A as another protocol surface, like the existing compatibility surfaces; translation only.
   - Opt-in, off by default; Agent Card at the standard location.
   - Link: [architecture.md](architecture.md), [mapping.md](mapping.md).
3. **Breaking changes**: none intended; new paths only; auth allowlist extended.
4. **Maintenance plan**
   - CI: the shared black-box suites, the official TCK, upstream-convention C++ and Python tests.
   - Human testers: needed once for a real-hardware pass.
   - Who maintains the protocol-version churn: state the commitment honestly.
5. **Risks**
   - Scope (an agent protocol is a scope increase by upstream's own definition); security (a new
     unauthenticated-by-default path class if the allowlist is missed); a second parser to maintain.

## Evidence table (all reproducible from this repository)

| Claim | Source |
|---|---|
| Official A2A TCK: 161 passed, 0 failed with the messageId workaround (157 passed, 0 failed, 4 expected-fail without) | [conformance.md](../conformance.md) |
| Independent SDK clients (CLI, JS, Go, .NET, Java) interoperate over both bindings | [interoperability.md](../interoperability.md) |
| Real Lemonade validation across three llama.cpp backends | [real-lemonade-validation.md](../real-lemonade-validation.md) |
| Adapter overhead about 5-15 ms TTFT; flat throughput under load | [benchmarks.md](../benchmarks.md) |
| Cancellation stops real backend generation (177 ms versus 30.9 s queued) | [benchmarks.md](../benchmarks.md) |
| Fuzzing found and fixed two defects | [roadmap.md](../roadmap.md) |

## Open questions to put to the maintainers

See "Questions only upstream maintainers can answer" in [decisions.md](decisions.md).

## Process notes

- Open as *Request for Comment (RFC)* in Discussions; tag the code owners for new endpoints, networking and
  security, and the CLI; mention it in the project's developer channel.
- Keep an "Update N" list at the top as the thread moves.
- Do not open a PR before the RFC carries the on-roadmap label.
- Offer to work under an existing working group if one fits.
