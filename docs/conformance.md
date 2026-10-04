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

## Pinning mock replies for the TCK

`tests/mock_lemonade.py` returns `MOCK_LEMONADE_OK` by default. To pin exact replies for a conformance run, set `MOCK_LEMONADE_RESPONSES` to a JSON object mapping prompt to reply, for example:

```bash
MOCK_LEMONADE_RESPONSES='{"TCK artifact test": "Generated text content"}' \
  python -m uvicorn tests.mock_lemonade:app --port 13305
```
