"""SDK-backed outbound A2A client.

Use the upstream A2A SDK for protocol negotiation, JSON-RPC, HTTP+JSON,
legacy 0.3 compatibility, task lifecycle and streaming. This facade owns
connection lifetime and injects outbound authentication and timeouts.
"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, Self
from urllib.parse import urlsplit

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.client.errors import A2AClientError, A2AClientTimeoutError, AgentCardResolutionError
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
)
from a2a.utils.constants import TransportProtocol

from lemonade_a2a.client._ssrf_transport import SSRFGuardedTransport
from lemonade_a2a.client.errors import (
    AgentAuthenticationError,
    AgentDiscoveryError,
    OutboundA2AError,
    RemoteAgentUnavailable,
    TaskExecutionError,
    UnsupportedProtocolError,
)
from lemonade_a2a.client.models import AgentReply
from lemonade_a2a.discovery import validate_remote_url

FAILED_STATES = frozenset({"TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"})


def translate_error(exc: BaseException) -> OutboundA2AError:
    """Map transport and SDK failures onto the stable outbound error taxonomy."""
    if isinstance(exc, OutboundA2AError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return AgentAuthenticationError(
            f"remote agent refused the caller (HTTP {exc.response.status_code})"
        )
    if isinstance(exc, httpx.TimeoutException | A2AClientTimeoutError | TimeoutError):
        return RemoteAgentUnavailable("remote agent did not respond in time")
    if isinstance(exc, AgentCardResolutionError):
        return AgentDiscoveryError(f"could not read the remote Agent Card: {exc}")
    if isinstance(exc, httpx.HTTPError):
        return RemoteAgentUnavailable(f"remote agent is unavailable ({type(exc).__name__})")
    if isinstance(exc, A2AClientError):
        text = str(exc)
        if "401" in text or "403" in text:
            return AgentAuthenticationError("remote agent refused the caller")
        return TaskExecutionError(f"remote agent returned an error: {text}")
    if isinstance(exc, ValueError) and "transport" in str(exc):
        return UnsupportedProtocolError("remote agent offers no A2A binding this client speaks")
    if isinstance(exc, ValueError):  # SSRF / URL policy
        return AgentDiscoveryError(str(exc))
    return TaskExecutionError(f"remote call failed ({type(exc).__name__})")


class A2AClient:
    """A single remote agent connection, opened lazily and closed explicitly.

    ``card`` (a JSON Agent Card that the caller already fetched and validated, for
    example from a marketplace listing) skips the SDK's own card lookup, so agents whose
    card does not live at ``<base_url>/.well-known/agent-card.json`` still work.
    """

    def __init__(
        self,
        base_url: str,
        *,
        streaming: bool = False,
        timeout: float = 30.0,
        bearer_token: str | None = None,
        allow_local: bool = False,
        allowed_hosts: frozenset[str] | None = None,
        card: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = validate_remote_url(base_url, allow_local=allow_local)
        self.streaming = streaming
        self.timeout = timeout
        self.bearer_token = bearer_token
        self.allow_local = allow_local
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts) if allowed_hosts else None
        host = (urlsplit(base_url).hostname or "").lower()
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            raise ValueError("Agent hostname is not on the approved allowlist")
        self.card = copy.deepcopy(card) if card is not None else None
        self._http: httpx.AsyncClient | None = None
        self._sdk: Any = None

    async def __aenter__(self) -> Self:
        if self._sdk is None:
            headers = {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}
            transport = SSRFGuardedTransport(
                allow_local=self.allow_local, allowed_hosts=self.allowed_hosts
            )
            self._http = httpx.AsyncClient(
                timeout=self.timeout,
                headers=headers,
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            )
            try:
                factory = ClientFactory(
                    ClientConfig(
                        streaming=self.streaming,
                        httpx_client=self._http,
                        supported_protocol_bindings=[
                            TransportProtocol.JSONRPC,
                            TransportProtocol.HTTP_JSON,
                        ],
                    )
                )
                if self.card is not None:
                    # parse_agent_card rewrites legacy 0.3 fields in place.
                    self._sdk = factory.create(parse_agent_card(copy.deepcopy(self.card)))
                else:
                    # Card lookup and transport negotiation handled by the SDK.
                    self._sdk = await factory.create_from_url(self.base_url)
            except BaseException:
                await self.aclose()
                raise
        return self

    async def aclose(self) -> None:
        sdk, self._sdk = self._sdk, None
        http, self._http = self._http, None
        try:
            if sdk is not None:
                await sdk.close()
        finally:
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
                await asyncio.sleep(0.25 * (2**attempt))
        raise AssertionError("unreachable")

    async def cancel_task(self, request: CancelTaskRequest) -> Any:
        """Request cancellation (do not blindly retry a failed request)."""
        return await self._connected().cancel_task(request)

    async def send_and_collect(self, request: SendMessageRequest) -> list[StreamResponse]:
        """Consume responses from the SDK (including streaming event sequences)."""
        return [event async for event in self.send_message(request)]

    async def ask(
        self,
        text: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
        parts: Sequence[Part] = (),
        wait_seconds: float = 120.0,
        poll_interval: float = 0.5,
    ) -> AgentReply:
        """Send one text message and wait for the remote agent's answer.

        A task that is still running after the send is polled (an idempotent read) until it
        reaches a terminal or interrupted state or ``wait_seconds`` pass. A failed, rejected
        or canceled task raises :class:`TaskExecutionError`; an ``auth-required`` one raises
        :class:`AgentAuthenticationError`. ``input-required`` is returned to the caller,
        who answers by calling ``ask`` again with that reply's ``task_id`` and ``context_id``.
        ``parts`` (file or data parts) are sent after the text.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("a non-empty text message is required")
        message = Message(
            role=Role.ROLE_USER,
            message_id=uuid.uuid4().hex,
            parts=[Part(text=text), *parts],
        )
        if context_id:
            message.context_id = context_id
        if task_id:
            message.task_id = task_id
        reply = AgentReply()
        deadline = time.monotonic() + wait_seconds
        try:
            async with asyncio.timeout(wait_seconds):
                async for event in self.send_message(SendMessageRequest(message=message)):
                    reply.add(event)
                while reply.task_id and not reply.finished:
                    await asyncio.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
                    reply.add_task(await self.get_task(GetTaskRequest(id=reply.task_id), retries=1))
        except Exception as exc:
            raise translate_error(exc) from exc
        if reply.state in FAILED_STATES:
            detail = reply.status_text or reply.state
            raise TaskExecutionError(f"remote task ended {reply.state}: {detail}")
        if reply.state == "TASK_STATE_AUTH_REQUIRED":
            raise AgentAuthenticationError(
                reply.status_text or "remote agent requires authentication"
            )
        if not reply.state:
            raise TaskExecutionError("remote agent sent no task or message")
        return reply
