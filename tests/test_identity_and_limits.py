"""Per-user task isolation, multiple API keys, rate limiting and security profiles."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lemonade_a2a.config import Settings, parse_api_keys
from lemonade_a2a.executor import LemonadeAgentExecutor
from lemonade_a2a.middleware import RateLimiter
from lemonade_a2a.server import build_agent_card, create_app, startup_warnings

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
ALICE = {**HEADERS, "Authorization": "Bearer alice-secret"}
BOB = {**HEADERS, "X-API-Key": "bob-secret"}


class EchoClient:
    async def stream(self, messages):
        yield "echo:" + messages[0]["content"]


def _app(**settings) -> TestClient:
    config = Settings(api_keys="alice:alice-secret,bob:bob-secret", **settings)
    return TestClient(create_app(config, executor=LemonadeAgentExecutor(EchoClient())))


def _send(client: TestClient, headers: dict, text: str = "hi") -> str:
    body = {"message": {"messageId": f"m-{text}", "role": "ROLE_USER", "parts": [{"text": text}]}}
    response = client.post("/message:send", headers=headers, content=json.dumps(body))
    assert response.status_code == 200, response.text
    return response.json()["task"]["id"]


def test_each_key_is_its_own_user() -> None:
    client = _app()

    assert client.get("/tasks", headers=ALICE).status_code == 200
    assert client.get("/tasks", headers=BOB).status_code == 200
    assert (
        client.get("/tasks", headers={**HEADERS, "Authorization": "Bearer nope"}).status_code == 401
    )
    assert client.get("/tasks", headers=HEADERS).status_code == 401


def test_users_cannot_read_list_or_cancel_each_others_tasks() -> None:
    client = _app()
    task_id = _send(client, ALICE)

    assert client.get(f"/tasks/{task_id}", headers=ALICE).status_code == 200

    # Bob gets exactly what he would get for a task that does not exist.
    assert client.get(f"/tasks/{task_id}", headers=BOB).status_code == 404
    assert client.get("/tasks", headers=BOB).json().get("tasks", []) == []
    assert client.post(f"/tasks/{task_id}:cancel", headers=BOB).status_code == 404
    jsonrpc = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": task_id}}
    reply = client.post("/", headers=BOB, content=json.dumps(jsonrpc)).json()
    assert reply["error"]["code"] == -32001  # TaskNotFound

    # Alice still sees her task, and only hers.
    assert [t["id"] for t in client.get("/tasks", headers=ALICE).json()["tasks"]] == [task_id]


def test_a_user_cannot_continue_another_users_task() -> None:
    client = _app()
    task_id = _send(client, ALICE)
    body = {
        "message": {
            "messageId": "hijack",
            "role": "ROLE_USER",
            "taskId": task_id,
            "parts": [{"text": "take over"}],
        }
    }

    response = client.post("/message:send", headers=BOB, content=json.dumps(body))

    assert response.status_code == 404


def test_single_api_key_is_one_shared_identity() -> None:
    client = TestClient(create_app(Settings(api_key="k")))
    headers = {**HEADERS, "Authorization": "Bearer k"}

    assert client.get("/tasks", headers=headers).status_code == 200


def test_card_declares_auth_when_only_api_keys_are_set() -> None:
    assert build_agent_card(Settings(api_keys="a:b")).security_requirements
    assert not build_agent_card(Settings()).security_requirements


@pytest.mark.parametrize(
    "spec",
    ["nokey", ":key", "name:", "a:1,a:2"],
)
def test_malformed_api_keys_are_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_api_keys(spec)


def test_default_is_reserved_for_the_single_key() -> None:
    with pytest.raises(ValueError, match="default"):
        _ = Settings(api_key="x", api_keys="default:y").credentials


def test_rate_limit_answers_429_with_retry_after_per_identity() -> None:
    client = _app(rate_limit_per_minute=3)

    codes = [client.get("/tasks", headers=ALICE).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]
    limited = client.get("/tasks", headers=ALICE)
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.json()["error"]["status"] == "RESOURCE_EXHAUSTED"
    # Bob has his own budget, and discovery is never limited.
    assert client.get("/tasks", headers=BOB).status_code == 200
    assert client.get("/.well-known/agent-card.json").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_unauthenticated_requests_do_not_spend_a_users_budget() -> None:
    client = _app(rate_limit_per_minute=2)

    for _ in range(10):
        assert client.get("/tasks", headers=HEADERS).status_code == 401
    assert client.get("/tasks", headers=ALICE).status_code == 200


def test_token_bucket_refills_over_time() -> None:
    now = [0.0]
    limiter = RateLimiter(60, clock=lambda: now[0])  # one request per second, burst of 60

    assert all(limiter.retry_after("a") == 0 for _ in range(60))
    assert limiter.retry_after("a") == pytest.approx(1.0)
    now[0] += 1.0
    assert limiter.retry_after("a") == 0
    assert limiter.retry_after("a") > 0


def test_rate_limiter_memory_is_bounded() -> None:
    limiter = RateLimiter(10, max_identities=100)

    for index in range(10_000):
        limiter.retry_after(f"client-{index}")

    assert len(limiter._buckets) <= 100


def test_rate_limiter_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)


# --- security profiles -------------------------------------------------------------


def test_local_profile_refuses_a_public_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        Settings(host="0.0.0.0")
    Settings(host="localhost")
    Settings(host="::1")


def test_lan_profile_needs_authentication() -> None:
    with pytest.raises(ValueError, match="authentication"):
        Settings(profile="lan", host="0.0.0.0")
    Settings(profile="lan", host="0.0.0.0", api_key="k")
    Settings(profile="lan", host="0.0.0.0", api_keys="a:b")


def test_external_profile_needs_auth_tls_and_rate_limit() -> None:
    base = {"profile": "external", "host": "0.0.0.0", "api_key": "k"}
    with pytest.raises(ValueError, match="TLS"):
        Settings(**base, rate_limit_per_minute=60)
    tls = {"ssl_certfile": "c.pem", "ssl_keyfile": "k.pem"}
    with pytest.raises(ValueError, match="rate limiting"):
        Settings(**base, **tls)
    with pytest.raises(ValueError, match="authentication"):
        Settings(profile="external", host="0.0.0.0", rate_limit_per_minute=60, **tls)
    Settings(**base, **tls, rate_limit_per_minute=60)


def test_unknown_profile_is_refused() -> None:
    with pytest.raises(ValueError, match="PROFILE"):
        Settings(profile="public")


def test_profile_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEMONADE_A2A_PROFILE", "LAN")
    monkeypatch.setenv("LEMONADE_A2A_HOST", "0.0.0.0")
    monkeypatch.setenv("LEMONADE_A2A_API_KEYS", "ops:secret")
    monkeypatch.setenv("LEMONADE_A2A_RATE_LIMIT_PER_MINUTE", "120")

    settings = Settings.from_env()

    assert settings.profile == "lan"
    assert settings.credentials == {"ops": "secret"}
    assert settings.rate_limit_per_minute == 120


def test_startup_warnings_match_the_profile() -> None:
    assert startup_warnings(Settings()) == []
    lan = Settings(profile="lan", host="0.0.0.0", api_key="k")
    assert any("TLS" in w for w in startup_warnings(lan))
    assert any("RATE_LIMIT" in w for w in startup_warnings(lan))
    hardened = Settings(
        profile="external",
        host="0.0.0.0",
        api_key="k",
        ssl_certfile="c",
        ssl_keyfile="k",
        rate_limit_per_minute=60,
    )
    assert startup_warnings(hardened) == []


def test_secrets_stay_out_of_repr() -> None:
    text = repr(Settings(api_key="topsecret", api_keys="a:hidden", lemonade_api_key="alsohidden"))

    assert "topsecret" not in text and "hidden" not in text
