from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class LemonadeClient:
    """Minimal client for Lemonade's OpenAI-compatible chat surface."""

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        # One pooled client: building an AsyncClient per request loads a fresh
        # SSL context, which costs hundreds of milliseconds of TTFT on Windows.
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _payload(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": messages, "stream": stream}
        if self.model:
            payload["model"] = self.model
        return payload

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        response = await self._client().post(
            f"{self.base_url}/chat/completions",
            json=self._payload(messages, stream=False),
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Yield text deltas from Lemonade's OpenAI-compatible SSE stream."""
        async with self._client().stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=self._payload(messages, stream=True),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
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
