from __future__ import annotations

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
from starlette.routing import BaseRoute, Mount

from . import __version__
from .config import Settings
from .executor import LemonadeAgentExecutor
from .lemonade_client import LemonadeClient
from .middleware import (
    BodySizeLimitMiddleware,
    add_agent_card_cache_headers,
    add_security_headers,
    normalize_rest_response,
    reject_unsupported_content_type,
    require_api_key,
)
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

    # No interactive docs / OpenAPI: the contract is the A2A Agent Card, and these
    # pages would only advertise internals (and stay open when no API key is set).
    app = FastAPI(
        lifespan=lifespan,
        title="Lemonade A2A",
        description="A2A v1 protocol surface for Lemonade local inference.",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.middleware("http")(add_security_headers)
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
    # Outermost: oversized bodies are refused before anything else reads them.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO)
    LOG.info("Starting Lemonade A2A on %s:%s", settings.host, settings.port)
    exposed = settings.host not in ("127.0.0.1", "localhost", "::1")
    if exposed and not settings.api_key:
        LOG.warning(
            "Listening on %s without LEMONADE_A2A_API_KEY: any peer that can reach this "
            "port can use the model. Set an API key and terminate TLS before exposing it.",
            settings.host,
        )
    if exposed and not settings.ssl_certfile:
        LOG.warning(
            "Listening on %s without TLS: credentials and prompts cross the network in "
            "clear text unless a TLS-terminating proxy sits in front.",
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
