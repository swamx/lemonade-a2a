# Interoperability

The TCK ([conformance.md](conformance.md)) checks the protocol surface against a test suite. This page records the other half: **independent official clients talking to a live Lemonade A2A adapter**, none of which share code with this repository's server.

_Run 2026-10-04 on Windows 11 against the adapter at the end of PR #11, with the deterministic mock backend and with a real Lemonade 2026.40.0 server (Bonsai-1.7B, llama.cpp)._

## Results

| Client | Version | Scenarios | Result |
|---|---|---|---|
| **A2A CLI** (Go SDK) | v0.3.0, release checksum verified | `card get`; `send` blocking and `--stream`; `--async` then `task get` / `task list`; `task cancel` on a running task; each over JSON-RPC **and** HTTP+JSON (`--transport jsonrpc` / `rest`) | Pass |
| A2A CLI, auth enabled | v0.3.0 | Direct `--endpoint` + `--auth "Bearer <key>"`, both bindings, real Lemonade; 401 without the key | Pass. Discovery by Agent Card **fails** on a card that declares a security scheme (upstream bug, fixed in a2a-go v2.6.0, see below) |
| A2A CLI rebuilt on a2a-go v2.6.0 | CLI source with `go get a2a-go/v2@v2.6.0` | Card discovery of the auth-declaring card, then `send` with `--auth` on both bindings, real Lemonade | **Pass**: the fixed SDK parses the card |
| **A2A JavaScript SDK** | `@a2a-js/sdk` 1.3.0 | send, streaming (13 chunks mock / ~50 real), get + list, cancel running task, unknown-task error; JSON-RPC **and** HTTP+JSON | **10/10** on the mock, **10/10** on real Lemonade with API-key auth |
| **A2A Go SDK as a library** | `a2a-go/v2` v2.6.0 (`interop/go`) | send, streaming (36 chunks), get + list, cancel running task, unknown-task error; JSON-RPC **and** HTTP+JSON | **10/10** on real Lemonade with API-key auth |
| **A2A .NET SDK** | NuGet `A2A` 1.0.0-preview2 (`interop/dotnet`, net8.0) | same five scenarios; JSON-RPC (`A2AClient`) **and** HTTP+JSON (`A2AHttpJsonClient`) | **10/10** on real Lemonade with API-key auth (preview SDK) |
| **A2A Java SDK** | `org.a2aproject.sdk` 1.4.0.Final (`interop/java`, JDK 21) | same five scenarios; JSON-RPC **and** HTTP+JSON | **10/10** on real Lemonade with API-key auth (see the `ListTasks` note below) |
| **A2A Inspector validators** | `backend/validators.py` from a2a-inspector `main` | Agent Card validation; validation of every event in a streamed response over both bindings | Pass on the mock and on real Lemonade (46-56 events per stream); the validators were also checked to reject a malformed card and task |

Reproduce:

```bash
# adapter running on :9100 (mock or real), then:
cd interop && npm install && node js_client.mjs http://127.0.0.1:9100 [--auth <key>]
python interop/inspector_validate.py --inspector ../a2a-inspector [--auth <key>]
# CLI: download a2a from https://github.com/a2aproject/a2a-cli/releases and run
a2a send "hello" -a http://127.0.0.1:9100 --transport rest --stream
# Go library, .NET and Java SDK clients (each exits non-zero on failure)
(cd interop/go && go run . -url http://127.0.0.1:9100 [-auth <key>])
(cd interop/dotnet && dotnet run -- http://127.0.0.1:9100/ [--auth <key>])
(cd interop/java && mvn -q package dependency:copy-dependencies -DoutputDirectory=target/lib \
   && java -cp "target/classes:target/lib/*" interop.Main http://127.0.0.1:9100 [--auth <key>])
```

## Findings

- **a2a-go could not parse a ProtoJSON security requirement (fixed upstream, re-tested).** With `LEMONADE_A2A_API_KEY` set the Agent Card declares a bearer scheme as `"securityRequirements": [{"schemes": {"bearer": {}}}]`, which is the A2A v1.0 ProtoJSON form (`map<string, StringList>`). The CLI v0.3.0 (bundling a2a-go v2.5.0) rejects the card (`A2ACLI_ERR_CARD_INVALID ... cannot unmarshal object into ... SecuritySchemeScopes`): [a2aproject/a2a-go#430](https://github.com/a2aproject/a2a-go/issues/430). The fix shipped in **a2a-go v2.6.0**; rebuilding the CLI against it parses the same card and completes authenticated sends on both bindings, and the Go library client above (v2.6.0) passes. The card was never changed because it is spec-correct. Until a CLI release bundles v2.6.0, connect with `--endpoint` instead of card discovery.
- **Java SDK 1.4.0 over-validates `ListTasks` responses.** Its JSON-RPC client throws `InvalidParamsError: pageSize must be equal to the number of tasks in the list` when it asks for the default page size (50) and the server returns fewer tasks. The A2A spec defines `pageSize` as the page size used and its own `ListTasks` example returns `pageSize: 10` with one task, and the adapter follows that, so this is an SDK strictness issue (HTTP+JSON is unaffected). The Java scenario filters by context with `pageSize=1` to exercise `ListTasks` regardless. Also, its JSON-RPC mapper throws a `NullPointerException` for a `ListTasksParams` with a null tenant; use an empty tenant.
- **Message IDs must be unique.** Replaying a multi-turn conversation in which every message reuses one `messageId` makes the SDK drop the repeats from task history. This is what the two SHOULD-level TCK deviations (`CORE-HIST-005/006`) turned out to be; see [conformance.md](conformance.md).

## Not run

| Item | Why |
|---|---|
| **ITK** (a2a-itk) | It verifies *SDK against SDK*: scenarios are nested traversal instructions executed by ITK's own agents, built on ITK's instruction protocol. A standalone adapter does not implement that, so there is nothing meaningful to point it at. Cross-SDK evidence is provided by the real clients above instead. |
| Rust, Python-client and other SDKs | No toolchain exercised; the adapter itself is built on the Python SDK server side. |
| Inspector web UI | Only its validator code was run, not the browser UI. |
| gRPC, push notifications, extended Agent Card | Not offered by the adapter. |
