"""HTTP middleware: protocol alignment, authentication and request hardening.

Order matters and is set in ``server.create_app`` (the last one added runs first).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections import OrderedDict
from collections.abc import Callable

from fastapi.responses import JSONResponse, Response
from starlette.authentication import SimpleUser
from starlette.requests import ClientDisconnect


async def normalize_rest_response(request, call_next):
    """Align HTTP+JSON responses with TCK expectations.

    Maps TASK_NOT_CANCELABLE to HTTP 409 and serves errors as application/json.
    """
    response = await call_next(request)
    path = request.url.path

    if path.endswith(":cancel") and response.status_code == 400:
        body = b"".join([chunk async for chunk in response.body_iterator])
        try:
            payload = json.loads(body)
            details = payload["error"]["details"]
            not_cancelable = any(
                isinstance(item, dict) and item.get("reason") == "TASK_NOT_CANCELABLE"
                for item in details
            )
        except (ValueError, KeyError, TypeError):
            payload, not_cancelable = None, False
        if not_cancelable:
            payload["error"]["code"] = 409
            return JSONResponse(payload, status_code=409)
        response = Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
        )

    if path.startswith(("/message:", "/tasks", "/extendedAgentCard", "/a2a/rest/")) and (
        response.headers.get("content-type", "").startswith("application/a2a+json")
    ):
        response.headers["content-type"] = "application/json"
    return response


PUBLIC_PATHS = frozenset({"/healthz", "/.well-known/agent-card.json"})


def _error_response(code: int, status: str, message: str, headers: dict | None = None):
    body = {"error": {"code": code, "status": status, "message": message}}
    return JSONResponse(body, status_code=code, headers=headers)


def require_api_key(api_keys: dict[str, str] | str):
    """Middleware factory: bearer / X-API-Key authentication (discovery stays public).

    ``api_keys`` maps an identity to its key; a bare string is the single-user
    shortcut. The identity becomes the request's user, which the A2A SDK uses as the
    task owner, so one caller cannot read, list or cancel another caller's tasks.
    """
    keys = {"default": api_keys} if isinstance(api_keys, str) else dict(api_keys)
    encoded = [(name, key.encode()) for name, key in keys.items()]

    async def middleware(request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        bearer = request.headers.get("authorization", "")
        supplied = bearer[7:] if bearer[:7].lower() == "bearer " else ""
        supplied = (supplied or request.headers.get("x-api-key", "")).encode()
        identity = None
        for name, key in encoded:  # compare against every key so timing does not reveal which
            if hmac.compare_digest(supplied, key):
                identity = name
        if identity is None:
            return _error_response(
                401, "UNAUTHENTICATED", "Authentication required", {"WWW-Authenticate": "Bearer"}
            )
        request.scope["user"] = SimpleUser(identity)
        return await call_next(request)

    return middleware


class RateLimiter:
    """Token bucket per identity: ``per_minute`` requests, refilled continuously.

    Memory is bounded: only the ``max_identities`` most recently seen identities
    are tracked, so a flood of distinct addresses cannot grow it without limit.
    """

    def __init__(
        self,
        per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_identities: int = 10_000,
    ) -> None:
        if per_minute < 1:
            raise ValueError("per_minute must be positive")
        self.capacity = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self._clock = clock
        self._max_identities = max_identities
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def retry_after(self, identity: str) -> float:
        """Seconds until ``identity`` may call again; 0 means the call is allowed (and counted)."""
        now = self._clock()
        tokens, stamp = self._buckets.pop(identity, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - stamp) * self.refill_per_second)
        if tokens >= 1.0:
            self._buckets[identity] = (tokens - 1.0, now)
            allowed = 0.0
        else:
            self._buckets[identity] = (tokens, now)
            allowed = (1.0 - tokens) / self.refill_per_second
        while len(self._buckets) > self._max_identities:
            self._buckets.popitem(last=False)
        return allowed


def rate_limit(limiter: RateLimiter):
    """Middleware factory: 429 with ``Retry-After`` once an identity exceeds its budget.

    Runs after authentication, so the identity is the API-key user; without keys it
    falls back to the client address.
    """

    async def middleware(request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        user = request.scope.get("user")
        identity = (
            user.display_name
            if user is not None
            else (request.client.host if request.client else "?")
        )
        wait = limiter.retry_after(identity)
        if wait:
            return _error_response(
                429,
                "RESOURCE_EXHAUSTED",
                "Rate limit exceeded",
                {"Retry-After": str(max(1, round(wait)))},
            )
        return await call_next(request)

    return middleware


DISCONNECT_KEY = "lemonade_a2a.disconnected"


class DisconnectSignalMiddleware:
    """Expose an ``asyncio.Event`` that is set when the client goes away.

    The event is stored in the ASGI scope and handed to the executor through the A2A
    call context. It only fires while something is reading the connection, which is
    the case for streaming (SSE) responses; it is what the opt-in
    ``cancel_on_disconnect`` mode listens to.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        disconnected = asyncio.Event()
        scope[DISCONNECT_KEY] = disconnected

        async def watching_receive():
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected.set()
            return message

        await self.app(scope, watching_receive, send)


JSON_MEDIA_TYPES = ("application/json", "application/a2a+json")
JSONRPC_PATHS = ("/", "/a2a/jsonrpc")
CONTENT_TYPE_NOT_SUPPORTED_MESSAGE = "Content-Type must be application/json"


def _has_body(request) -> bool:
    length = request.headers.get("content-length")
    return bool(length and length != "0") or "transfer-encoding" in request.headers


async def reject_unsupported_content_type(request, call_next):
    """Answer ContentTypeNotSupported for non-JSON request bodies.

    JSON-RPC uses error -32005; HTTP+JSON uses 415 with an AIP-193 error body.
    Body-less POSTs (``:cancel``, ``:subscribe``) are not affected.
    """
    if request.method == "POST" and _has_body(request):
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type not in JSON_MEDIA_TYPES:
            if request.url.path in JSONRPC_PATHS:
                error = {"code": -32005, "message": CONTENT_TYPE_NOT_SUPPORTED_MESSAGE}
                return JSONResponse({"jsonrpc": "2.0", "id": None, "error": error})
            error = {
                "code": 415,
                "status": "INVALID_ARGUMENT",
                "message": CONTENT_TYPE_NOT_SUPPORTED_MESSAGE,
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "CONTENT_TYPE_NOT_SUPPORTED",
                        "domain": "a2a-protocol.org",
                        "metadata": {},
                    }
                ],
            }
            return JSONResponse({"error": error}, status_code=415)
    return await call_next(request)


async def reject_invalid_utf8(request, call_next):
    """Answer a parse error for request bodies that are not valid UTF-8.

    Without this the A2A SDK's decoder raises and the caller gets an internal error
    (HTTP 500 on HTTP+JSON, -32603 on JSON-RPC) for what is plain bad input.
    """
    if request.method == "POST" and _has_body(request):
        try:
            (await request.body()).decode("utf-8")
        except ClientDisconnect:  # the body limiter already answered 413 and cut the stream
            return Response(status_code=400)
        except UnicodeDecodeError:
            message = "Request body is not valid UTF-8"
            if request.url.path in JSONRPC_PATHS:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": message}}
                )
            return _error_response(400, "INVALID_ARGUMENT", message)
    return await call_next(request)


AGENT_CARD_PATH = "/.well-known/agent-card.json"
AGENT_CARD_MAX_AGE_SECONDS = 300


def add_agent_card_cache_headers(last_modified: str):
    """Middleware factory: cacheability headers for the (static) Agent Card (spec 8.6.1)."""

    async def middleware(request, call_next):
        response = await call_next(request)
        if request.url.path == AGENT_CARD_PATH and response.status_code == 200:
            response.headers["cache-control"] = f"public, max-age={AGENT_CARD_MAX_AGE_SECONDS}"
            response.headers["last-modified"] = last_modified
        return response

    return middleware


class BodySizeLimitMiddleware:
    """Reject request bodies over ``max_bytes`` with 413 before they are buffered.

    Checks ``Content-Length`` up front and counts streamed (chunked) bodies. When a
    stream crosses the limit the 413 is sent immediately and the application sees a
    disconnect, so it stops reading and the client cannot make the server hold an
    arbitrarily large JSON document in memory.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        started = rejected = False

        async def limited_receive():
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    rejected = True
                    if not started:
                        await self._reject(scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message):
            nonlocal started
            if rejected:  # our 413 is already on the wire; drop whatever the app tries to say
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, limited_receive, tracking_send)

    async def _reject(self, scope, receive, send) -> None:
        error = {"code": 413, "status": "INVALID_ARGUMENT", "message": "Request body too large"}
        response = JSONResponse({"error": error}, status_code=413, headers={"Connection": "close"})
        await response(scope, receive, send)


async def add_security_headers(request, call_next):
    """Defensive headers for an API that returns only JSON / event streams."""
    response = await call_next(request)
    response.headers.setdefault("x-content-type-options", "nosniff")
    response.headers.setdefault("referrer-policy", "no-referrer")
    if request.url.path != AGENT_CARD_PATH:
        response.headers.setdefault("cache-control", "no-store")
    return response
