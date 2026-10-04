# Interoperability

The TCK ([conformance.md](conformance.md)) checks the protocol surface against a test suite. This page records the other half: **independent official clients talking to a live Lemonade A2A adapter**, none of which share code with this repository's server.

_Run 2026-10-04 on Windows 11 against the adapter at the end of PR #11, with the deterministic mock backend and with a real Lemonade 2026.40.0 server (Bonsai-1.7B, llama.cpp)._

## Results

| Client | Version | Scenarios | Result |
|---|---|---|---|
| **A2A CLI** (Go SDK) | v0.3.0, release checksum verified | `card get`; `send` blocking and `--stream`; `--async` then `task get` / `task list`; `task cancel` on a running task; each over JSON-RPC **and** HTTP+JSON (`--transport jsonrpc` / `rest`) | Pass |
| A2A CLI, auth enabled | v0.3.0 | Direct `--endpoint` + `--auth "Bearer <key>"`, both bindings, real Lemonade; 401 without the key | Pass. Discovery by Agent Card **fails** on a card that declares a security scheme (upstream bug, see below) |
| **A2A JavaScript SDK** | `@a2a-js/sdk` 1.3.0 | send, streaming (13 chunks mock / ~50 real), get + list, cancel running task, unknown-task error; JSON-RPC **and** HTTP+JSON | **10/10** on the mock, **10/10** on real Lemonade with API-key auth |
| **A2A Inspector validators** | `backend/validators.py` from a2a-inspector `main` | Agent Card validation; validation of every event in a streamed response over both bindings | Pass on the mock and on real Lemonade (46-56 events per stream); the validators were also checked to reject a malformed card and task |

Reproduce:

```bash
# adapter running on :9100 (mock or real), then:
cd interop && npm install && node js_client.mjs http://127.0.0.1:9100 [--auth <key>]
python interop/inspector_validate.py --inspector ../a2a-inspector [--auth <key>]
# CLI: download a2a from https://github.com/a2aproject/a2a-cli/releases and run
a2a send "hello" -a http://127.0.0.1:9100 --transport rest --stream
```

## Findings

- **a2a-go cannot parse a ProtoJSON security requirement.** With `LEMONADE_A2A_API_KEY` set the Agent Card declares a bearer scheme as `"securityRequirements": [{"schemes": {"bearer": {}}}]`, which is the A2A v1.0 ProtoJSON form (`map<string, StringList>`). The CLI v0.3.0's Go SDK rejects the card (`A2ACLI_ERR_CARD_INVALID ... cannot unmarshal object into ... SecuritySchemeScopes`). This is the upstream issue [a2aproject/a2a-go#430](https://github.com/a2aproject/a2a-go/issues/430) (closed upstream, so a newer Go SDK should be fixed). The card is spec-correct and the JS SDK and Inspector read it, so it was left unchanged. Workaround: connect with `--endpoint` instead of card discovery. Re-test when a CLI release with the fix ships.
- **Message IDs must be unique.** Replaying a multi-turn conversation in which every message reuses one `messageId` makes the SDK drop the repeats from task history. This is what the two SHOULD-level TCK deviations (`CORE-HIST-005/006`) turned out to be; see [conformance.md](conformance.md).

## Not run

| Item | Why |
|---|---|
| **ITK** (a2a-itk) | It verifies *SDK against SDK*: scenarios are nested traversal instructions executed by ITK's own agents, built on ITK's instruction protocol. A standalone adapter does not implement that, so there is nothing meaningful to point it at. Cross-SDK evidence is provided by the real clients above instead. |
| .NET SDK | Only a preview package (`1.0.0-preview2`) is published. |
| Java and Go SDK libraries | No JDK or Go toolchain on the test machine (Go is covered through the CLI binary). |
| Inspector web UI | Only its validator code was run, not the browser UI. |
| gRPC, push notifications, extended Agent Card | Not offered by the adapter. |
