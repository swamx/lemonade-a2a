from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock Lemonade")

DEFAULT_TEXT = "MOCK_LEMONADE_OK"


def _canned_responses() -> dict[str, str]:
    """Prompt -> reply overrides from MOCK_LEMONADE_RESPONSES (a JSON object).

    Lets conformance runs (e.g. the A2A TCK) pin exact replies without
    hardcoding any suite-specific prompt in the mock itself.
    """
    try:
        value = json.loads(os.environ.get("MOCK_LEMONADE_RESPONSES", "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    stream = bool(payload.get("stream"))
    messages = payload.get("messages") or []
    canned = _canned_responses()
    override = next(
        (
            canned[message["content"]]
            for message in messages
            if isinstance(message, dict) and message.get("content") in canned
        ),
        None,
    )
    text = override if override is not None else DEFAULT_TEXT

    if not stream:
        return JSONResponse(
            {
                "id": "mock-completion",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
            }
        )

    async def events():
        count = int(os.environ.get("MOCK_LEMONADE_TOKEN_COUNT", "0"))
        if override is not None:
            tokens = [text]
        elif count > 0:
            tokens = [f"tok{i} " for i in range(count)]
        else:
            tokens = ["MOCK_", "LEMONADE_", "OK"]
        for token in tokens:
            if await request.is_disconnected():
                print("MOCK_CLIENT_DISCONNECTED", flush=True)
                return
            event = {
                "id": "mock-stream",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": token}}],
            }
            yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(float(os.environ.get("MOCK_LEMONADE_TOKEN_DELAY", "0.01")))
        print("MOCK_STREAM_COMPLETE", flush=True)
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
