"""SDK-backed outbound A2A client.

Use the upstream A2A SDK for protocol negotiation, JSON-RPC, HTTP+JSON,
legacy 0.3 compatibility, task lifecycle and streaming. This facade owns
connection lifetime and injects outbound authentication and timeouts.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    SendMessageRequest,
    StreamResponse,
)

from lemonade_a2a.discovery import validate_remote_url


class A2AClient:
    """A single remote agent connection, opened lazily and closed explicitly."""

    def __init__(
        self,
        base_url: str,
        *,
        streaming: bool = False,
        timeout: float = 30.0,
        bearer_token: str | None = None,
        allow_local: bool = False,
    ) -> None:
        self.base_url = validate_remote_url(base_url, allow_local=allow_local)
        self.streaming = streaming
        self.timeout = timeout
        self.bearer_token = bearer_token
        self._http: httpx.AsyncClient | None = None
        self._sdk: Any = None

    async def __aenter__(self) -> "A2AClient":
        if self._sdk is None:
            headers = {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}
            self._http = httpx.AsyncClient(
                timeout=self.timeout, headers=headers, follow_redirects=False, trust_env=False
            )
            try:
                factory = ClientFactory(
                    ClientConfig(streaming=self.streaming, httpx_client=self._http)
                )
                # Card lookup and transport negotiation handled by the SDK.
                self._sdk = await factory.create_from_url(self.base_url)
            except BaseException:
                await self.aclose()
                raise
        return self

    async def aclose(self) -> None:
        sdk, self._sdk = self._sdk, None
        http, self._http = self._http, None
        if sdk is not None:
            await sdk.close()
        if http is not None:
            await http.aclose()

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _connected(self) -> Any:
        if self._sdk is None:
            raise RuntimeError("Use 'async with A2AClient(...)' before making requests")
        return self._sdk

    async def send_message(self, request: SendMessageRequest) -> AsyncIterator[StreamResponse]:
        """Send a message; SDK may return a message, task, or streaming updates."""
        async for result in self._connected().send_message(request):
            yield result

    async def get_task(self, request: GetTaskRequest, *, retries: int = 0) -> Any:
        """Retry transient failures only for an idempotent read, never for writes."""
        if not 0 <= retries <= 3:
            raise ValueError("retries must be between 0 and 3")
        for attempt in range(retries + 1):
            try:
                return await self._connected().get_task(request)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == retries:
                    raise
                await asyncio.sleep(0.25 * (2 ** attempt))
        raise AssertionError("unreachable")

    async def cancel_task(self, request: CancelTaskRequest) -> Any:
        """Request cancellation (do not blindly retry a failed request)."""
        return await self._connected().cancel_task(request)

    async def send_and_collect(self, request: SendMessageRequest) -> list[StreamResponse]:
        """Consume responses from the SDK (including streaming event sequences)."""
        return [event async for event in self.send_message(request)]
