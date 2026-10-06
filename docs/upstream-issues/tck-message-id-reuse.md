# Upstream issue: TCK reuses one `messageId` for several messages (CORE-HIST-005/006)

**Filed 2026-10-05 as https://github.com/a2aproject/a2a-tck/issues/248.** Found while running the TCK at commit
`263b9cfaf16a554bdfb166a7ba5b67716e946349` against Lemonade A2A (see `docs/conformance.md`).

---

## Title

`CORE-HIST-005/006` multi-turn history tests send three different messages with the same `messageId`

## Summary

`create_multiturn_task_with_history` (`tests/compatibility/_task_helpers.py`) sends an initial message and then two follow-ups. All three use `tck_id("input-required")`, which returns `tck-input-required-<SESSION>`, so **three distinct messages share one `messageId`**. A2A requires `messageId` to identify a message uniquely, and SDK-based servers (observed with a2a-sdk for Python 1.2) treat a repeated `messageId` on an existing task as a duplicate and do not append it to the task history.

The tests then look for the follow-up texts in `GetTask` history, do not find them, and report the SHOULD-level requirements as failing (`xfail`). Servers that behave correctly end up marked non-compliant.

## Evidence

Result of the TCK run (both JSON-RPC and HTTP+JSON):

```
CORE-HIST-005: Could not find enough known messages in history to verify ordering;
               found texts: ['TCK prerequisite task creation', 'TCK complete after history']
CORE-HIST-006: Expected message 'TCK history message 1' not found in history; ...
```

Reproduction against any SUT: send `SendMessage` with `messageId = "tck-input-required-X"` (text "initial"), then two follow-ups on the same `taskId` that also use `messageId = "tck-input-required-X"` (texts "TCK history message 1/2"), then a completing message. `GetTask` history contains the initial and the completing message only.

Replaying the same conversation with a unique `messageId` per message returns all four messages in order on both transports, so the server's history handling is correct.

## Suggested fix

Give each message in `create_multiturn_task_with_history` (and any other helper that sends several messages in one task) its own id, for example:

```python
"messageId": f"{tck_id('input-required')}-{index}",
```

(or `uuid.uuid4().hex`). The SUT scenario matching on the `tck-input-required` **prefix** keeps working because the prefix is unchanged.

## Environment

- TCK `263b9cf`, run against a2a-sdk (Python) 1.2.x behind FastAPI.
- Observed on both `jsonrpc` and `http_json` transports.
