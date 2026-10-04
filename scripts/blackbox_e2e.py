from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

import httpx
from a2a_util import artifact_text

A2A_BASE = os.getenv("A2A_BASE_URL", "http://127.0.0.1:9100")
EXPECTED = os.getenv("EXPECTED_TEXT", "MOCK_LEMONADE_OK")


async def wait_ready(client: httpx.AsyncClient, url: str, attempts: int = 60) -> None:
    for _ in range(attempts):
        try:
            response = await client.get(url)
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.25)
    raise RuntimeError(f"service did not become ready: {url}")


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        await wait_ready(client, f"{A2A_BASE}/healthz")

        card_response = await client.get(f"{A2A_BASE}/.well-known/agent-card.json")
        card_response.raise_for_status()
        card = card_response.json()
        assert card["name"] == "Lemonade Local Agent"

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "ROLE_USER",
                    "parts": [{"text": "Return the deterministic response."}],
                }
            },
        }
        response = await client.post(
            A2A_BASE,
            headers={"A2A-Version": "1.0"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise RuntimeError(json.dumps(body["error"], indent=2))
        text = artifact_text(body.get("result"))
        if EXPECTED not in text:
            raise AssertionError(f"expected {EXPECTED!r} in A2A result, got: {body!r}")
        print(f"PASS: Agent Card + A2A message/send + Lemonade SSE => {EXPECTED}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise
