from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
import json

import httpx


class LemonadeClient:
    """Minimal client for Lemonade's OpenAI-compatible chat surface."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _payload(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": messages, "stream": stream}
        if self.model:
            payload["model"] = self.model
        return payload

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=self._payload(messages, stream=False),
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Yield text deltas from Lemonade's OpenAI-compatible SSE stream."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=self._payload(messages, stream=True),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    event = json.loads(raw)
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        yield text
