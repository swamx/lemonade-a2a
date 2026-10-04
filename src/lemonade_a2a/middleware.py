"""HTTP middleware: protocol alignment, authentication and request hardening.

Order matters and is set in ``server.create_app`` (the last one added runs first).
"""

from __future__ import annotations

import hmac
import json

from fastapi.responses import JSONResponse, Response


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


def require_api_key(api_key: str):
    """Middleware factory: bearer / X-API-Key authentication (discovery stays public)."""
    expected = api_key.encode()

    async def middleware(request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        bearer = request.headers.get("authorization", "")
        supplied = bearer[7:] if bearer[:7].lower() == "bearer " else ""
        supplied = supplied or request.headers.get("x-api-key", "")
        if not hmac.compare_digest(supplied.encode(), expected):
            error = {"code": 401, "status": "UNAUTHENTICATED", "message": "Authentication required"}
            return JSONResponse(
                {"error": error}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
        return await call_next(request)

    return middleware


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
