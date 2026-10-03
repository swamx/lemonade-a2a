from __future__ import annotations

import json

import httpx
import pytest

from lemonade_a2a.lemonade_client import LemonadeClient
from tests.mock_lemonade import app


@pytest.mark.asyncio
async def test_non_streaming_mock_backend() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock") as http:
        response = await http.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "MOCK_LEMONADE_OK"


@pytest.mark.asyncio
async def test_streaming_mock_backend_is_openai_compatible() -> None:
    transport = httpx.ASGITransport(app=app)
    chunks: list[str] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://mock") as http:
        async with http.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    event = json.loads(line[6:])
                    chunks.append(event["choices"][0]["delta"]["content"])
    assert "".join(chunks) == "MOCK_LEMONADE_OK"
