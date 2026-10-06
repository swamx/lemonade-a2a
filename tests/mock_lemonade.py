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


@app.get("/api/v1/health")
async def health() -> dict:
    """Shaped like Lemonade Server 2026.40 (version and status are what ``doctor`` reads)."""
    return {
        "status": "ok",
        "version": os.environ.get("MOCK_LEMONADE_VERSION", "2026.40.0"),
        "model_loaded": None,
        "all_models_loaded": [],
    }


@app.get("/api/v1/system-info")
async def system_info() -> dict:
    return {"OS Version": "MockOS", "devices": {"cpu": {"available": True}}}


@app.get("/v1/models")
async def models() -> dict:
    context = int(os.environ.get("MOCK_LEMONADE_CONTEXT", "32768"))
    return {
        "object": "list",
        "data": [{"id": "mock-model", "object": "model", "context_length": context}],
    }


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
        if os.environ.get("MOCK_LEMONADE_ERROR"):
            # What real Lemonade does for a prompt longer than the loaded context: an error
            # event inside an HTTP 200 stream.
            error = {
                "code": 400,
                "type": os.environ["MOCK_LEMONADE_ERROR"],
                "message": "request (1647 tokens) exceeds the available context size (1536 tokens)",
            }
            yield f"data: {json.dumps({'error': error})}\n\n"
            return
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
