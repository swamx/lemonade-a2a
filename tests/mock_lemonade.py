from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock Lemonade")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    payload = await request.json()
    stream = bool(payload.get("stream"))
    text = "MOCK_LEMONADE_OK"

    if not stream:
        return JSONResponse(
            {
                "id": "mock-completion",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
            }
        )

    async def events():
        for token in ["MOCK_", "LEMONADE_", "OK"]:
            if await request.is_disconnected():
                return
            event = {
                "id": "mock-stream",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": token}}],
            }
            yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(0.01)
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
