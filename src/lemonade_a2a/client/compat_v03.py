"""Minimal outbound A2A 0.3 JSON-RPC client for trusted remote agents.

This intentionally keeps remote transport separate from the Lemonade inference
backend. Full 1.x outbound support can be added as another transport adapter.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from lemonade_a2a.client._ssrf_transport import SSRFGuardedTransport
from lemonade_a2a.discovery import validate_remote_url


class RemoteA2AError(RuntimeError):
    """Remote A2A invocation failed or returned an invalid response."""


class RemoteA2AClient:
    def __init__(self, endpoint: str, *, timeout: float = 30.0, allow_local: bool = False) -> None:
        self.endpoint = validate_remote_url(endpoint, allow_local=allow_local)
        self.timeout = timeout
        self.allow_local = allow_local

    async def send_message(self, parts: list[dict[str, Any]]) -> dict[str, Any]:
        """Send protocol 0.3 message parts to a trusted A2A endpoint.

        The reusable API takes protocol parts, not merchant-specific actions.
        Callers own negotiation of the remote Agent Card and protocol version.
        """
        if not parts or any(not isinstance(part, dict) or "kind" not in part for part in parts):
            raise ValueError("At least one well-formed A2A message part is required")
        request_id = uuid.uuid4().hex
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": uuid.uuid4().hex,
                    "role": "user",
                    "parts": parts,
                }
            },
        }
        transport = SSRFGuardedTransport(allow_local=self.allow_local)
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=False, transport=transport, trust_env=False
        ) as client:
            response = await client.post(self.endpoint, json=payload)
            response.raise_for_status()
            envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0":
            raise RemoteA2AError("Unexpected remote JSON-RPC response")
        if envelope.get("id") != request_id:
            raise RemoteA2AError("Remote JSON-RPC response identifier mismatch")
        if envelope.get("error"):
            raise RemoteA2AError(f"Remote A2A error: {envelope['error']}")
        if not isinstance(envelope.get("result"), dict):
            raise RemoteA2AError("Remote A2A result is missing or invalid")
        return envelope["result"]

    async def send_data(self, action: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Convenience wrapper for provider-defined action/input DataParts.

        The action/input convention is not defined by the A2A protocol.
        """
        if not action or not isinstance(inputs, dict):
            raise ValueError("action and inputs are required")
        return await self.send_message(
            [{"kind": "data", "data": {"action": action, "input": inputs}}]
        )
