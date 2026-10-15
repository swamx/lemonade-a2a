# Task state, streaming bridge and cancellation

**Status: draft (2026-10-15).** The behavior contract is the adapter's
[behavior tables](../protocol-mapping.md#implemented-behavior); this document says how native would own it.

## State ownership (decision D3)

**Recommendation: `A2aApi` owns a bounded in-memory task store; do not build on the job system.**

*Verified* from upstream's `docs/dev/job-system.md`: the job engine runs **client-posted recipes** (ordered steps
passing data through a context), a **single worker runs one job at a time**, lifecycle verbs are
pause/interrupt/resume/delete, and its `chat` op returns the backend response verbatim. That is the opposite of
A2A's needs: concurrent tasks, token streaming, and subscribers that come and go. Its durability across restarts
is attractive, but it can be added later behind the same store interface (the adapter already ships a SQLite
store as a reference second implementation).

| Task field | Purpose |
|---|---|
| id, context id, owner | Identity; owner is the isolation key (see [mapping.md](mapping.md) for the single-key gap) |
| state | `SUBMITTED`, `WORKING`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED` |
| artifacts | `lemonade-response` (and `lemonade-reasoning` when enabled) |
| event log | Ordered events with a sequence number, for any number of subscribers |
| cancel flag | `std::atomic<bool>`, set by `CancelTask`, the deadline and shutdown |
| deadline, timestamps | `a2a.max_task_seconds` |

Store rules carried over: bounded size, evict the oldest *finished* tasks first, never a running one; running
tasks capped (`REJECTED` beyond the cap, "retry later"); another owner's id behaves as unknown.
Restart behavior of the in-memory store: tasks are gone (the adapter's SQLite store marks interrupted `WORKING`
tasks `FAILED`; that stays an option).

## Streaming bridge

```text
SendStreamingMessage ─► validate ─► create task ─► runner thread ─► Router (chat completion, stream)
                                          │                              │ deltas (content / reasoning / error)
   subscribers (SSE) ◄── event log ◄──────┴────── bridge: delta → artifact chunk, state → status update
```

- One `lemonade-response` artifact chunk per content delta, a closing empty chunk with `last_chunk=true`;
  reasoning is dropped by default or streamed as a separate artifact closed before the answer (same as the adapter).
- A failure after some output closes the artifact (`last_chunk=true`) **then** sets `FAILED`; a failure before
  any output creates no artifact. A cancelled stream is left as streamed.
- An error event inside a 200 stream (context overflow and similar) becomes `FAILED` with the client-safe message
  and the detail only in Lemonade's log.
- Backpressure: a subscriber that stops reading must not stall the runner or other subscribers. The adapter gets
  this from the A2A SDK's bounded queue (1,024 events); native must provide an equivalent bound per subscriber and
  drop (evict) a slow subscriber rather than block generation.

**Mechanism risk (assumed, to be proved by the prototype).** *Verified:* the Router's streaming entry point is
`chat_completion_stream(request_body, httplib::DataSink&)`: it writes SSE into an HTTP sink. The bridge therefore
needs either a sink whose `write` parses the SSE it receives (an in-process adapter around the same code path the
OpenAI endpoint uses) or a delta-level callback added to the Router. The first is non-invasive; the second is a
Router change that belongs in its own PR.

## Cancellation

| Trigger | Action | Result |
|---|---|---|
| `CancelTask` on a running task | set the task's cancel flag; the runner stops the Router request | `CANCELED`; the real backend generation stops |
| `CancelTask` on a finished task | none | HTTP+JSON 409 `TASK_NOT_CANCELABLE`; JSON-RPC error |
| Deadline reached | same path | `FAILED` ("time limit") |
| Server shutdown | cancel all, bounded wait | tasks end terminal |
| Client disconnect | nothing by default (tasks outlive connections and can be resubscribed); with `a2a.cancel_on_disconnect`, cancel the task *that stream started* | as `CancelTask` |

Rules: cancellation must **never wait on a shared Router lock**; the flag is read by the runner, and any blocking
backend call is interrupted by the same mechanism upstream already uses for a closed connection
(`RequestCancelToken` and `std::atomic<bool>* cancel`, *verified* in `server.cpp` and `router.h`). Two cancels
in a row are safe. The adapter's proof (`benchmarks/cancellation_proof.py`: 30.9 s queued behind a running
generation versus 177 ms right after `CancelTask`) is the acceptance test; it needs only an A2A URL.

## Limits and errors

Same table as the adapter: text parts only (others `ContentTypeNotSupported`), empty or oversized input
`InvalidParams`, concurrency cap `REJECTED`, deadline `FAILED`, body not UTF-8 `-32700` or 400, sanitized
messages. No file or URL part is fetched until the SSRF policy (`safe_urls.py`, 50 cases) is ported with its tests.

## Model selection

A message names no model. Follow MCP's resolution precedence (*verified* in `mcp_server.h`): an explicit model
(from message metadata) wins, else a model of the right type that is already loaded, else one that is downloaded,
else fail the task with a clear "no model available" message; never trigger a multi-gigabyte download implicitly.
