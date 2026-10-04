from __future__ import annotations

import json
import logging

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from .config import Settings
from .executor import LemonadeAgentExecutor
from .lemonade_client import LemonadeClient

LOG = logging.getLogger("lemonade_a2a")


def build_agent_card(settings: Settings) -> AgentCard:
    return AgentCard(
        name=settings.agent_name,
        description=settings.agent_description,
        version="0.1.0",
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


def create_app() -> FastAPI:
    settings = Settings.from_env()
    agent_card = build_agent_card(settings)
    client = LemonadeClient(settings.lemonade_base_url, settings.model)
    request_handler = DefaultRequestHandler(
        agent_executor=LemonadeAgentExecutor(client),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    app = FastAPI(
        title="Lemonade A2A",
        description="A2A v1 protocol surface for Lemonade local inference.",
        version="0.1.0",
    )

    @app.middleware("http")
    async def normalize_rest_json_content_type(request, call_next):
        response = await call_next(request)
        path = request.url.path

        if path.endswith(":cancel") and response.status_code == 400:
            body = b"".join([chunk async for chunk in response.body_iterator])
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None
            details = (payload or {}).get("error", {}).get("details", [])
            if any(item.get("reason") == "TASK_NOT_CANCELABLE" for item in details):
                payload["error"]["code"] = 409
                return JSONResponse(payload, status_code=409)
            response = Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
            )

        if path.startswith(("/message:", "/tasks", "/extendedAgentCard", "/a2a/rest/")) and response.headers.get(
            "content-type", ""
        ).startswith("application/a2a+json"):
            response.headers["content-type"] = "application/json"
        return response

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
        rest_routes=[
            *create_rest_routes(
                request_handler=request_handler,
                path_prefix="",
                enable_v0_3_compat=False,
            ),
            *create_rest_routes(
                request_handler=request_handler,
                path_prefix="/a2a/rest",
                enable_v0_3_compat=False,
            ),
        ],
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO)
    LOG.info("Starting Lemonade A2A on %s:%s", settings.host, settings.port)
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
