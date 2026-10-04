from __future__ import annotations

import json

import httpx
import pytest

from lemonade_a2a.lemonade_client import LemonadeClient


@pytest.mark.asyncio
async def test_stream_yields_only_text_deltas(monkeypatch) -> None:
    events = [
        {"choices": [{"delta": {"content": "Hello"}}]},
        {"choices": [{"delta": {"content": " world"}}]},
        {"choices": [{"delta": {}}]},
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = LemonadeClient("http://localhost:8000/v1", "test-model")
    chunks = [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
    assert chunks == ["Hello", " world"]


@pytest.mark.asyncio
async def test_client_reuses_one_http_client_until_closed() -> None:
    client = LemonadeClient("http://localhost:8000/v1", "test-model")
    first = client._client()

    assert client._client() is first
    await client.aclose()
    assert client._client() is not first
    await client.aclose()
