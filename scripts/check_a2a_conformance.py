"""Black-box A2A v1 conformance smoke checks.

This is intentionally not a replacement for the official A2A TCK. It catches
integration regressions quickly and produces deterministic evidence before the
full external TCK/ITK jobs run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import httpx


async def check(base_url: str) -> None:
    base_url = base_url.rstrip("/")
    headers = {"A2A-Version": "1.0"}

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        health = await client.get(f"{base_url}/healthz")
        health.raise_for_status()

        card = await client.get(f"{base_url}/.well-known/agent-card.json")
        card.raise_for_status()
        card_data = card.json()
        interfaces = card_data.get("supportedInterfaces") or []
        versions = {item.get("protocolVersion") for item in interfaces}
        assert "1.0" in versions, f"Agent Card does not advertise A2A 1.0: {versions}"

        request_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": message_id,
                    "role": "ROLE_USER",
                    "parts": [{"kind": "text", "text": "Reply with: lemonade-a2a-ok"}],
                }
            },
        }
        response = await client.post(base_url, json=payload)
        response.raise_for_status()
        body = response.json()
        assert body.get("jsonrpc") == "2.0"
        assert body.get("id") == request_id
        assert "error" not in body, json.dumps(body, indent=2)

    print("A2A v1 smoke conformance: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9000")
    args = parser.parse_args()
    asyncio.run(check(args.base_url))


if __name__ == "__main__":
    main()
