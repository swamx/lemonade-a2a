from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx


class LemonadeClient:
    """Minimal client for Lemonade's OpenAI-compatible chat surface."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        payload: dict[str, Any] = {"messages": messages, "stream": False}
        if self.model:
            payload["model"] = self.model

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

        return data["choices"][0]["message"]["content"]

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Yield raw SSE data payloads. A2A event translation is handled above this layer."""
        payload: dict[str, Any] = {"messages": messages, "stream": True}
        if self.model:
            payload["model"] = self.model

        async with httpx.AsyncClient(timeout=self.timeout) as client, client.stream(
            "POST", f"{self.base_url}/chat/completions", json=payload
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    value = line[6:]
                    if value != "[DONE]":
                        yield value
