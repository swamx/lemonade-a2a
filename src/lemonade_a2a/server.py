from __future__ import annotations

import logging
import ssl
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
from .call_context import LemonadeCallContextBuilder
from .config import Settings
from .executor import LemonadeAgentExecutor
from .lemonade_client import LemonadeClient
from .logging_setup import configure_logging
from .middleware import (
    BodySizeLimitMiddleware,
    DisconnectSignalMiddleware,
    RateLimiter,
    TelemetryMiddleware,
    add_agent_card_cache_headers,
    add_security_headers,
    normalize_rest_response,
    rate_limit,
    reject_invalid_utf8,
    reject_unsupported_content_type,
    require_api_key,
)
from .task_store import BoundedTaskStore
from .telemetry import Telemetry, prometheus_payload, setup_telemetry

LOG = logging.getLogger("lemonade_a2a")


def _security(settings: Settings) -> dict:
    """Declare bearer auth on the card only when the server actually enforces it."""
    if not settings.credentials:
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


def _rest_routes(request_handler: DefaultRequestHandler, context_builder=None) -> list[BaseRoute]:
    """HTTP+JSON routes at the base URL plus the legacy ``/a2a/rest`` prefix.

    Each ``create_rest_routes`` call ends with a catch-all ``Mount("/{tenant}")``
    that answers 404 itself for any one-segment prefix, so it must come after
    every plain route or it shadows them (``/tasks/x`` as tenant ``tasks``).
    """
    legacy = create_rest_routes(
        request_handler=request_handler,
        path_prefix="/a2a/rest",
        enable_v0_3_compat=False,
        context_builder=context_builder,
    )
    base = create_rest_routes(
        request_handler=request_handler,
        path_prefix="",
        enable_v0_3_compat=False,
        context_builder=context_builder,
    )
    plain = [route for route in (*legacy, *base) if not isinstance(route, Mount)]
    return [*plain, *(route for route in base if isinstance(route, Mount))]


def create_app(
    settings: Settings | None = None,
    executor: AgentExecutor | None = None,
    telemetry: Telemetry | None = None,
) -> FastAPI:
    """Build the A2A app. ``executor`` lets tests/conformance runs swap the Lemonade executor;
    ``telemetry`` defaults to whatever ``settings`` asks for (off unless LEMONADE_A2A_OTEL=1)."""
    settings = settings or Settings.from_env()
    telemetry = telemetry if telemetry is not None else setup_telemetry(settings)
    agent_card = build_agent_card(settings)
    client = LemonadeClient(
        settings.lemonade_base_url,
        settings.model,
        timeout=settings.request_timeout_seconds,
        api_key=settings.lemonade_api_key,
        telemetry=telemetry,
    )
    agent_executor = executor or LemonadeAgentExecutor(
        client,
        max_input_chars=settings.max_input_chars,
        max_input_parts=settings.max_input_parts,
        max_task_seconds=settings.max_task_seconds,
        max_concurrent_tasks=settings.max_concurrent_tasks,
        cancel_on_disconnect=settings.cancel_on_disconnect,
        reasoning=settings.reasoning,
        telemetry=telemetry,
    )
    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=BoundedTaskStore(settings.max_stored_tasks, telemetry=telemetry),
        agent_card=agent_card,
    )
    context_builder = LemonadeCallContextBuilder()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        shutdown = getattr(agent_executor, "shutdown", None)
        if shutdown is not None:
            await shutdown()
        await client.aclose()
        telemetry.shutdown()

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
    app.middleware("http")(reject_invalid_utf8)
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
                context_builder=context_builder,
            ),
            *create_jsonrpc_routes(
                request_handler=request_handler,
                rpc_url="/a2a/jsonrpc",
                enable_v0_3_compat=False,
                context_builder=context_builder,
            ),
        ],
        rest_routes=_rest_routes(request_handler, context_builder),
    )

    if settings.rate_limit_per_minute:
        # Inside authentication, so the budget is per authenticated identity.
        app.middleware("http")(rate_limit(RateLimiter(settings.rate_limit_per_minute), telemetry))
    if settings.credentials:
        # Added last so it is outermost: unauthenticated requests do no other work.
        app.middleware("http")(require_api_key(settings.credentials, telemetry))
    app.add_middleware(DisconnectSignalMiddleware)
    # Outermost: oversized bodies are refused before anything else reads them.
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes, telemetry=telemetry
    )
    # Outermost of all, so refused requests (401, 413, 429) are traced and counted too.
    app.add_middleware(TelemetryMiddleware, telemetry=telemetry)
    app.state.telemetry = telemetry

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if telemetry.runtime is not None and telemetry.runtime.prometheus:

        @app.get("/metrics", include_in_schema=False)
        async def metrics_endpoint():
            from fastapi.responses import Response

            body, content_type = prometheus_payload(telemetry.runtime.prometheus_registry)
            return Response(body, media_type=content_type)

    return app


def uvicorn_options(settings: Settings) -> dict:
    """Listener options: host/port, TLS and, when required, mutual TLS."""
    options: dict = {
        "host": settings.host,
        "port": settings.port,
        "ssl_certfile": settings.ssl_certfile or None,
        "ssl_keyfile": settings.ssl_keyfile or None,
    }
    if settings.ssl_ca_certs:
        options["ssl_ca_certs"] = settings.ssl_ca_certs
    if settings.ssl_require_client_cert:
        options["ssl_cert_reqs"] = (
            ssl.CERT_REQUIRED
        )  # the handshake fails without a valid client cert
    return options


def startup_warnings(settings: Settings) -> list[str]:
    """Advice for deployments the chosen profile allows but does not make safe on its own."""
    warnings = []
    if settings.profile != "local" and not settings.ssl_certfile:
        warnings.append(
            f"profile {settings.profile!r} without TLS: credentials and prompts cross the "
            "network in clear text unless a TLS-terminating proxy sits in front."
        )
    if settings.profile == "lan" and not settings.rate_limit_per_minute:
        warnings.append(
            "profile 'lan' without LEMONADE_A2A_RATE_LIMIT_PER_MINUTE: one client can "
            "occupy the model; the concurrency cap only bounds how many tasks run."
        )
    return warnings


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    configure_logging(settings.log_format)
    telemetry = setup_telemetry(settings, set_global=True)
    LOG.info(
        "Starting Lemonade A2A on %s:%s (profile %s)",
        settings.host,
        settings.port,
        settings.profile,
    )
    for warning in startup_warnings(settings):
        LOG.warning("%s", warning)
    uvicorn.run(create_app(settings, telemetry=telemetry), **uvicorn_options(settings))


if __name__ == "__main__":
    main()
