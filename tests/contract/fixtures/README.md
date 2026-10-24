# Contract fixtures

Recorded and hand-authored backend streams, used by `tests/contract/test_backend_contract.py` to pin what the
adapter must make of each. They are plain files so **any implementation can replay them**, including a native
(C++) stream bridge; see [docs/native/validation-and-migration.md](../../../docs/native/validation-and-migration.md).

## Layout

```text
fixtures/<flavour>/<version-or-name>/<case>.sse    raw OpenAI-style SSE body, exactly as received
fixtures/<flavour>/<version-or-name>/<case>.json   what an implementation must make of it
```

`<flavour>` is `lemonade` (recorded from a real server, directory named by Lemonade version), `openai`,
`llama_cpp` or `vllm` (hand-authored shapes of other OpenAI-compatible servers).

## Expectation file (`<case>.json`)

| Field | Meaning |
|---|---|
| `answer` | The user-visible text: every `delta.content`, concatenated in order |
| `reasoning` | Every `delta.reasoning_content`, concatenated; **never** part of `answer` |
| `usage` | `{"input": n, "output": n}` when the stream reports token usage, else `null` (nothing is estimated) |
| `outcome` | `completed` or `failed` |
| `failure` | When `failed`: the exact client-safe message the task must carry (details stay in the log) |
| `recorded` | Provenance (server, version, model); informational |

## What a replaying implementation must do

1. Feed the `.sse` body as the backend stream and collect content and reasoning separately.
2. Treat an `error` object inside a 200 stream as `failed` with the `failure` message, never as an empty
   completion.
3. Close any open answer artifact before the task becomes `failed`.
4. Never include `reasoning` in the answer.

Adding a Lemonade release: record its fixtures with `scripts/record_contract_fixtures.py`; a version listed as
tested without recordings fails `test_every_tested_lemonade_version_has_recorded_fixtures`.
