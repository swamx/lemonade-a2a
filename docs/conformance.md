# A2A Conformance and Interoperability

Lemonade A2A targets the stable A2A 1.0 protocol family. Patch releases of the specification do not change the negotiated protocol version: clients and servers negotiate `1.0`.

## Three test layers

### 1. In-repository smoke conformance

`scripts/check_a2a_conformance.py` validates the deployment boundary quickly:

- health endpoint;
- Agent Card discovery;
- Agent Card advertises protocol version `1.0`;
- requests carry `A2A-Version: 1.0`;
- JSON-RPC `SendMessage` succeeds without a protocol error.

This suite is intentionally small and is not presented as protocol certification.

### 2. Official A2A TCK

> **Status (2026-10-04):** run against TCK commit `263b9cf`: **157 passed, 0 failed, 4 expected-fail (SHOULD), 104 skipped**. Results: [conformance-results.json](conformance-results.json) (summary) and [tck-compatibility-report.json](tck-compatibility-report.json) (full TCK report).
>
> **Scope matters.** The TCK drives behavior through `messageId` prefixes (file artifacts, input-required, rejection, ...) that an LLM adapter does not implement, so it runs against `tck/tck_sut.py`: the **real server layer** (routes, Agent Card, version negotiation, error mapping, content types, auth middleware) with a **scenario executor** in place of the Lemonade executor. It certifies the protocol surface; Lemonade inference is validated separately (mock E2E and real-Lemonade runs). It is not an official certification.
>
> | Transport | Requirements passed | Failed | Skipped |
> |---|---|---|---|
> | Agent Card | 8 | 0 | 0 |
> | JSON-RPC | 65 | 0 MUST (2 SHOULD) | 14 |
> | HTTP+JSON | 62 | 0 MUST (2 SHOULD) | 13 |
> | gRPC | - | - | 60 (not offered) |
>
> **Skipped** = capability not declared (gRPC, push notifications, extended agent card), a required extension not declared, tests needing a non-streaming agent, and TLS/auth/signature suites the TCK does not exercise. The TCK's own `overall_compatibility` percentage counts those as not passing; read the table above instead.
>
> **Optional capabilities: decision (2026-10-04) is to leave them undeclared.** The Agent Card advertises only what works end to end, so the skipped TCK tests are expected, not gaps:
>
> | Capability | Decision | Why, and what would change it |
> |---|---|---|
> | gRPC binding | Not offered | Lemonade's own surface is HTTP, and a gRPC server adds a dependency and a second listener for no current consumer. Revisit if a client needs it. |
> | Push notifications | Not supported (`pushNotifications: false`) | The server would call back URLs supplied by the client, which is an SSRF risk until the security profiles in the roadmap define an allowlist and private-network rules. |
> | Extended Agent Card | Not declared | There is no authenticated-only information to expose; the public card is complete. Revisit with per-user identity. |
>
> **Known deviations (SHOULD, expected-fail):** `CORE-HIST-005` and `CORE-HIST-006` (multi-turn history ordering/content) on both transports. **Root cause (2026-10-04): a TCK test artifact, not an adapter defect.** The TCK helper builds every `messageId` as `tck-<name>-<session>`, so the initial message and both follow-ups of its multi-turn task share one `messageId` (`tck-input-required-<session>`). The a2a-sdk treats a repeated `messageId` as a duplicate and does not append it to task history; replaying the same conversation with unique ids records all four messages in order on both transports. The TCK reports `['TCK prerequisite task creation', 'TCK complete after history']` because the two follow-ups were deduplicated. Reported upstream as [a2aproject/a2a-tck#248](https://github.com/a2aproject/a2a-tck/issues/248).
>
> **Workaround while #248 is open.** `tck/patches/unique-message-ids.patch` gives each follow-up its own `messageId` (a two-line change to the TCK's `_task_helpers.py`); `python scripts/run_tck.py --tck-dir <a2a-tck> --patch tck/patches/unique-message-ids.patch` applies it for one run and reverts it afterwards. Two runs are recorded and both are enforced in CI (`tck.yml`):
>
> | Run | Result | File |
> |---|---|---|
> | Official, unpatched | 157 passed, 0 failed, 4 expected-fail (`CORE-HIST-005/006` on both transports). Fails on **any other** deviation | [conformance-results.json](conformance-results.json) |
> | With the messageId patch | **161 passed, 0 failed, 0 deviations** | [conformance-results-patched.json](conformance-results-patched.json) |
>
> The patched run is the proof that the adapter's history handling is correct; the unpatched run stays the official number, because a patched TCK is not the TCK. When the patch no longer applies, the TCK has changed: re-check #248, then refresh or delete the patch and the expected deviations in `scripts/run_tck.py`.
>
> **Run it:** `python scripts/run_tck.py --tck-dir <a2a-tck clone with its own venv>` (see the script docstring). Not run: ITK / cross-SDK interoperability.

The official `a2aproject/a2a-tck` is the authoritative external conformance gate. Challenge and release reports must record:

- TCK repository revision;
- A2A specification revision vendored by that TCK;
- Lemonade A2A commit;
- transport tested;
- pass/fail/skip counts;
- known deviations with links to upstream issues when applicable.

Do not simply report “A2A compliant” without the test revision and results.

### 3. Official A2A ITK / cross-SDK interoperability

Conformance is necessary but not sufficient. Run interoperability scenarios against independent official SDK implementations, prioritizing Python, JS, Go and Java clients across supported transports.

## Version negotiation

A2A 1.0 clients send `A2A-Version: 1.0` on requests. The server must advertise `1.0` in its supported interfaces and reject unsupported major/minor protocol versions according to the specification/SDK behavior.

Patch versions such as `1.0.1` are specification releases, not negotiated wire versions.

## Release evidence

Each tagged Lemonade A2A release should publish a machine-readable summary similar to:

```json
{
  "a2a_protocol": "1.0",
  "implementation_commit": "<sha>",
  "tck_commit": "<sha>",
  "transports": {
    "jsonrpc": {"passed": 0, "failed": 0, "skipped": 0},
    "http_json": {"passed": 0, "failed": 0, "skipped": 0}
  }
}
```

The benchmark and challenge submission should link to this evidence rather than relying on screenshots.

## Pinning mock replies

`tests/mock_lemonade.py` returns `MOCK_LEMONADE_OK` by default (the TCK run above does not use it; it uses `tck/tck_sut.py`). To pin exact replies for other runs, set `MOCK_LEMONADE_RESPONSES` to a JSON object mapping prompt to reply, for example:

```bash
MOCK_LEMONADE_RESPONSES='{"TCK artifact test": "Generated text content"}' \
  python -m uvicorn tests.mock_lemonade:app --port 13305
```

## Optional capabilities, re-evaluated (2026-10-06)

The decision table above was re-checked against what the installed `a2a-sdk` (1.2.x) now offers (`lemonade-a2a doctor --sdk-gap` lists it) and what the adapter has gained since: per-user identities, a tested SSRF policy ([`safe_urls.py`](../src/lemonade_a2a/safe_urls.py)), rate limiting and mutual TLS.

| Capability | SDK offers | Decision | What would change it |
|---|---|---|---|
| gRPC binding | `grpc_handler` | **Still not offered.** Lemonade's own surface is HTTP, no client has asked, and it adds a dependency and a second listener (with its own auth, TLS and size limits to harden and test) | A concrete client that needs gRPC |
| Push notifications | sender and config stores | **Still not offered.** The URL policy now exists and is tested, but it is not wired in: a sender also needs per-task config storage with ownership, redirect and size handling, retry limits and an allowlist for private networks (the deployment, not the adapter, knows which are legitimate) | A deployment that needs server-initiated delivery; wire `safe_urls` in first and run the TCK's push tests |
| Extended Agent Card | `extended_card_modifier` hook | **Still not offered.** Per-user identities now exist, but there is no information that differs per user | A feature whose description depends on the caller (for example per-user model access) |
| Input-mode validation in the handler | `validate_input_modes` | Not used: the executor validates parts itself and rejects non-text parts with `CONTENT_TYPE_NOT_SUPPORTED` on both bindings (tested) | Richer parts |

The feature registry records each of these as `unsupported` with the reason, and `doctor --adapter-url` fails if a running adapter's card declares one of them.
