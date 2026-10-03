"""Minimal end-to-end smoke client for Lemonade A2A.

Run the Lemonade server and lemonade-a2a first, then execute:

    python examples/a2a_client.py
"""

from __future__ import annotations

import asyncio
import uuid

import httpx

A2A_URL = "http://127.0.0.1:9000/a2a/jsonrpc"


async def main() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": "In one sentence, what is AMD Lemonade?"}],
            }
        },
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(A2A_URL, json=payload)
        response.raise_for_status()
        print(response.json())


if __name__ == "__main__":
    asyncio.run(main())
