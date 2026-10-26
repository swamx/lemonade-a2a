"""Minimal outbound A2A 0.3 JSON-RPC client for trusted remote agents.

This intentionally keeps remote transport separate from the Lemonade inference
backend. Full 1.x outbound support can be added as another transport adapter.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx


class RemoteA2AError(RuntimeError):
    """Remote A2A invocation failed or returned an invalid response."""


class RemoteA2AClient:
    def __init__(self, endpoint: str, *, timeout: float = 30.0) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Remote A2A endpoint must use HTTPS")
        self.endpoint = endpoint
        self.timeout = timeout

    async def send_data(self, action: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Send a protocol 0.3 data part to a preapproved remote endpoint."""
        if not action or not isinstance(inputs, dict):
            raise ValueError("action and inputs are required")
        message_id = uuid.uuid4().hex
        payload = {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": uuid.uuid4().hex,
                    "role": "user",
                    "parts": [{
                        "kind": "data",
                        "data": {"action": action, "input": inputs},
                    }],
                }
            },
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            response = await client.post(self.endpoint, json=payload)
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
            raise RemoteA2AError("Unexpected remote JSON-RPC response")
        if result.get("id") != message_id:
            raise RemoteA2AError("Remote JSON-RPC response identifier mismatch")
        if result.get("error"):
            raise RemoteA2AError(f"Remote A2A error: {result['error']}")
        if "result" not in result:
            raise RemoteA2AError("Remote A2A result is missing")
        return result["result"]
