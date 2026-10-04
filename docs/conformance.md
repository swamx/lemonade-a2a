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
> **Known deviations (SHOULD, expected-fail):** `CORE-HIST-005` and `CORE-HIST-006` (multi-turn history ordering/content) on both transports. Not yet root-caused: they depend on how the SDK and the scenario executor record follow-up messages in task history.
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
