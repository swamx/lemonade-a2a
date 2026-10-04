from fastapi.testclient import TestClient

from lemonade_a2a.config import Settings
from lemonade_a2a.server import build_agent_card, create_app


def test_agent_card_advertises_a2a_v1() -> None:
    card = build_agent_card(Settings())
    versions = {interface.protocol_version for interface in card.supported_interfaces}
    assert "1.0" in versions


def test_agent_card_has_jsonrpc_and_http_json_bindings() -> None:
    card = build_agent_card(Settings())
    bindings = {interface.protocol_binding for interface in card.supported_interfaces}
    assert "JSONRPC" in bindings
    assert "HTTP+JSON" in bindings
    assert all(
        interface.url == "http://localhost:9000"
        for interface in card.supported_interfaces
    )


def test_protocol_routes_match_the_advertised_base_url() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/" in paths
    assert "/message:send" in paths
    assert "/a2a/jsonrpc" in paths
    assert "/a2a/rest/message:send" in paths


def test_http_json_errors_use_application_json() -> None:
    response = TestClient(create_app()).get(
        "/tasks/missing",
        headers={"A2A-Version": "1.0"},
    )

    assert response.headers["content-type"].startswith("application/json")


def test_not_cancelable_maps_to_409_and_rest_content_type() -> None:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from lemonade_a2a.server import normalize_rest_response

    app = FastAPI()
    app.middleware("http")(normalize_rest_response)

    @app.post("/tasks/t1:cancel")
    async def cancel() -> JSONResponse:
        body = {"error": {"code": 400, "details": [{"reason": "TASK_NOT_CANCELABLE"}]}}
        return JSONResponse(body, status_code=400, media_type="application/a2a+json")

    @app.post("/tasks/t2:cancel")
    async def other() -> JSONResponse:
        return JSONResponse({"error": "bad"}, status_code=400)

    client = TestClient(app)
    response = client.post("/tasks/t1:cancel")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == 409
    assert client.post("/tasks/t2:cancel").status_code == 400
