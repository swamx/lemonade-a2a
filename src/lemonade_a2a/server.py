from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from email.utils import formatdate

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount

from . import __version__
from .config import Settings
from .executor import LemonadeAgentExecutor
from .lemonade_client import LemonadeClient
from .task_store import BoundedTaskStore

LOG = logging.getLogger("lemonade_a2a")


def _security(settings: Settings) -> dict:
    """Declare bearer auth on the card only when the server actually enforces it."""
    if not settings.api_key:
        return {}
    return {
        "security_schemes": {
            "bearer": SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer")
            )
        },
        "security_requirements": [SecurityRequirement(schemes={"bearer": StringList()})],
    }


def build_agent_card(settings: Settings) -> AgentCard:
    return AgentCard(
        name=settings.agent_name,
        description=settings.agent_description,
        version=__version__,
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text"],
        default_output_modes=["text", "task-status"],
        skills=[
            AgentSkill(
                id="local-chat",
                name="Local AI",
                description="Language-model inference executed by Lemonade on local hardware.",
                tags=["local-ai", "lemonade"],
                examples=["Explain why local inference is useful."],
                input_modes=["text"],
                output_modes=["text", "task-status"],
            )
        ],
        **_security(settings),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=settings.public_url,
            ),
            AgentInterface(
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
                url=settings.public_url,
            ),
        ],
    )


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


def _rest_routes(request_handler: DefaultRequestHandler) -> list[BaseRoute]:
    """HTTP+JSON routes at the base URL plus the legacy ``/a2a/rest`` prefix.

    Each ``create_rest_routes`` call ends with a catch-all ``Mount("/{tenant}")``
    that answers 404 itself for any one-segment prefix, so it must come after
    every plain route or it shadows them (``/tasks/x`` as tenant ``tasks``).
    """
    legacy = create_rest_routes(
        request_handler=request_handler, path_prefix="/a2a/rest", enable_v0_3_compat=False
    )
    base = create_rest_routes(
        request_handler=request_handler, path_prefix="", enable_v0_3_compat=False
    )
    plain = [route for route in (*legacy, *base) if not isinstance(route, Mount)]
    return [*plain, *(route for route in base if isinstance(route, Mount))]


def create_app(settings: Settings | None = None, executor: AgentExecutor | None = None) -> FastAPI:
    """Build the A2A app. ``executor`` lets tests/conformance runs swap the Lemonade executor."""
    settings = settings or Settings.from_env()
    agent_card = build_agent_card(settings)
    client = LemonadeClient(
        settings.lemonade_base_url,
        settings.model,
        timeout=settings.request_timeout_seconds,
        api_key=settings.lemonade_api_key,
    )
    agent_executor = executor or LemonadeAgentExecutor(
        client,
        max_input_chars=settings.max_input_chars,
        max_input_parts=settings.max_input_parts,
        max_task_seconds=settings.max_task_seconds,
        max_concurrent_tasks=settings.max_concurrent_tasks,
    )
    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=BoundedTaskStore(settings.max_stored_tasks),
        agent_card=agent_card,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        shutdown = getattr(agent_executor, "shutdown", None)
        if shutdown is not None:
            await shutdown()
        await client.aclose()

    app = FastAPI(
        lifespan=lifespan,
        title="Lemonade A2A",
        description="A2A v1 protocol surface for Lemonade local inference.",
        version=__version__,
    )

    app.middleware("http")(reject_unsupported_content_type)
    app.middleware("http")(normalize_rest_response)
    # The card only changes with configuration, i.e. at process start.
    app.middleware("http")(add_agent_card_cache_headers(formatdate(usegmt=True)))

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=agent_card),
        jsonrpc_routes=[
            *create_jsonrpc_routes(
                request_handler=request_handler,
                rpc_url="/",
                enable_v0_3_compat=False,
            ),
            *create_jsonrpc_routes(
                request_handler=request_handler,
                rpc_url="/a2a/jsonrpc",
                enable_v0_3_compat=False,
            ),
        ],
        rest_routes=_rest_routes(request_handler),
    )

    if settings.api_key:
        # Added last so it is outermost: unauthenticated requests do no other work.
        app.middleware("http")(require_api_key(settings.api_key))

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO)
    LOG.info("Starting Lemonade A2A on %s:%s", settings.host, settings.port)
    if not settings.api_key and settings.host not in ("127.0.0.1", "localhost", "::1"):
        LOG.warning(
            "Listening on %s without LEMONADE_A2A_API_KEY: any peer that can reach this "
            "port can use the model. Set an API key and terminate TLS before exposing it.",
            settings.host,
        )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        ssl_certfile=settings.ssl_certfile or None,
        ssl_keyfile=settings.ssl_keyfile or None,
    )


if __name__ == "__main__":
    main()
