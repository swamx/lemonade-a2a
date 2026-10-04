from fastapi.testclient import TestClient

from lemonade_a2a.config import Settings
from lemonade_a2a.server import build_agent_card, create_app

KEY = "test-key-123"
HEADERS = {"A2A-Version": "1.0"}


def _client(api_key: str = KEY) -> TestClient:
    return TestClient(create_app(Settings(api_key=api_key)))


def test_requests_without_credentials_are_rejected() -> None:
    response = _client().get("/tasks", headers=HEADERS)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["status"] == "UNAUTHENTICATED"


def test_wrong_key_is_rejected_on_every_binding() -> None:
    client = _client()
    bad = {**HEADERS, "Authorization": "Bearer nope"}

    assert client.get("/tasks", headers=bad).status_code == 401
    assert client.post("/", headers=bad, json={}).status_code == 401
    assert client.get("/a2a/rest/tasks", headers=bad).status_code == 401


def test_bearer_and_x_api_key_are_accepted() -> None:
    client = _client()
    bearer = {**HEADERS, "Authorization": f"Bearer {KEY}"}

    assert client.get("/tasks", headers=bearer).status_code == 200
    assert client.get("/tasks", headers={**HEADERS, "X-API-Key": KEY}).status_code == 200


def test_discovery_and_health_stay_public() -> None:
    client = _client()

    assert client.get("/healthz").status_code == 200
    assert client.get("/.well-known/agent-card.json").status_code == 200


def test_no_key_configured_means_open_access() -> None:
    assert _client("").get("/tasks", headers=HEADERS).status_code == 200


def test_card_declares_bearer_scheme_only_when_enforced() -> None:
    secured = build_agent_card(Settings(api_key=KEY))
    open_card = build_agent_card(Settings())

    assert "bearer" in secured.security_schemes
    assert secured.security_schemes["bearer"].http_auth_security_scheme.scheme == "Bearer"
    assert not open_card.security_schemes


def test_api_keys_are_not_in_settings_repr() -> None:
    assert KEY not in repr(Settings(api_key=KEY, lemonade_api_key=KEY))
