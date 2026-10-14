# Draft upstream issue: make the per-task event queue size configurable in the v2 request handler

**Not filed.** Draft for the repository owner to review and, if they agree, post at
https://github.com/a2aproject/a2a-python/issues. Found while building slow-consumer backpressure tests
for lemonade-a2a against `a2a-sdk` 1.2.1 and 1.2.2 (see `tests/test_live_lifecycle.py` and the P1 roadmap).

---

## Title

`DefaultRequestHandler` (v2) ignores `queue_manager`, so the per-task event queue bound (1,024) cannot be configured

## Summary

Passing a `queue_manager` to `DefaultRequestHandler` produces

```
DeprecationWarning: A queue_manager was passed to DefaultRequestHandlerV2, but it is not used: v2 delegates
event streaming to an in-memory ActiveTaskRegistry, so custom or distributed QueueManager implementations
are ignored. ...
```

`ActiveTask` then builds its queues with the module default (`EventQueueSource()` /
`DEFAULT_MAX_QUEUE_SIZE = 1024`), and nothing on `DefaultRequestHandler`, `ActiveTaskRegistry` or
`ActiveTask` lets an application change it.

## Why it matters

The bounded queue is what gives a streaming server backpressure: when a client connects and stops reading, the
queue fills, the producer (the agent executor) blocks and stops pulling tokens from the model, and a slow
subscriber is evicted. We verified this end to end and it works well. But 1,024 events is a fixed trade-off:

* a deployment streaming single tokens may want a smaller bound (less memory per stalled client, faster
  eviction); one streaming large chunks may want a different one;
* with many concurrent tasks the worst-case memory is `tasks x 1,024 x event size`, which the operator cannot
  tune.

## Evidence

A client that opens `POST /message:stream` and never reads, against an agent producing 300,000 small events with
no delay: the SDK evicts the stalled sink ("queue full at delivery time"), process memory stays flat (about
80 MB), the server keeps answering other callers, and the task ends at its own deadline. So the mechanism is
sound; only its size is fixed.

## Suggested change

Accept an optional size where the queues are created, for example `DefaultRequestHandler(...,
max_event_queue_size: int = DEFAULT_MAX_QUEUE_SIZE)` passed to `ActiveTaskRegistry` and on to
`EventQueueSource`. Keep the default so nothing changes for existing users.

## Environment

`a2a-sdk` 1.2.1 and 1.2.2, Python 3.11 to 3.13, FastAPI/Starlette routes via `create_jsonrpc_routes` and
`create_rest_routes`.
