from __future__ import annotations

from typing import Any

from .lemonade_client import LemonadeClient


def text_from_message(message: Any) -> str:
    """Extract text conservatively from an A2A SDK message-like object.

    A2A SDK types are intentionally isolated at the server boundary while the
    project validates v1 API stability.
    """
    parts = getattr(message, "parts", None) or []
    values: list[str] = []
    for part in parts:
        root = getattr(part, "root", part)
        text = getattr(root, "text", None)
        if isinstance(text, str):
            values.append(text)
    return "\n".join(values)


class LemonadeAgent:
    """Protocol-neutral execution core used by the A2A adapter."""

    def __init__(self, client: LemonadeClient) -> None:
        self.client = client

    async def invoke(self, user_text: str) -> str:
        if not user_text.strip():
            raise ValueError("A2A message did not contain a non-empty text part")
        return await self.client.chat([{"role": "user", "content": user_text}])
