import importlib
import importlib.metadata
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

import lemonade_a2a
from lemonade_a2a import server
from lemonade_a2a.config import Settings
from lemonade_a2a.lemonade_client import LemonadeClient
from lemonade_a2a.server import create_app

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


def _client(**settings) -> TestClient:
    return TestClient(create_app(Settings(**settings)))


def test_interactive_docs_and_openapi_are_not_exposed() -> None:
    client = _client()

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_responses_carry_security_headers() -> None:
    client = _client()
    api = client.get("/tasks", headers=HEADERS)
    card = client.get("/.well-known/agent-card.json")

    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["referrer-policy"] == "no-referrer"
    assert api.headers["cache-control"] == "no-store"
    # The card stays cacheable (spec 8.6.1) but still gets nosniff.
    assert "max-age=" in card.headers["cache-control"]
    assert card.headers["x-content-type-options"] == "nosniff"


def test_oversized_body_is_rejected_by_content_length() -> None:
    client = _client(max_request_bytes=1000)

    response = client.post("/", headers=HEADERS, content=b"x" * 5000)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == 413


def test_oversized_chunked_body_is_rejected() -> None:
    client = _client(max_request_bytes=1000)

    def chunks():
        for _ in range(10):
            yield b"x" * 500

    response = client.post("/", headers=HEADERS, content=chunks())

    assert response.status_code == 413


def test_small_bodies_still_work_under_the_limit() -> None:
    client = _client(max_request_bytes=1000)
    body = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "nope"}}

    response = client.post("/", headers=HEADERS, content=json.dumps(body))

    assert response.status_code == 200
    assert "error" in response.json()


def test_body_limit_applies_before_authentication() -> None:
    client = _client(max_request_bytes=100, api_key="k")

    assert client.post("/", headers=HEADERS, content=b"x" * 500).status_code == 413


def test_non_http_scopes_pass_through_the_body_limit() -> None:
    # Lifespan runs through the same middleware stack; it must not be disturbed.
    with TestClient(create_app(Settings(max_request_bytes=100))) as client:
        assert client.get("/healthz").status_code == 200


def test_max_request_bytes_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Settings(max_request_bytes=0)


def test_main_passes_tls_and_warns_when_exposed(monkeypatch, caplog) -> None:
    captured = {}
    monkeypatch.setenv("LEMONADE_A2A_HOST", "0.0.0.0")
    monkeypatch.delenv("LEMONADE_A2A_API_KEY", raising=False)
    monkeypatch.delenv("LEMONADE_A2A_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("LEMONADE_A2A_SSL_KEYFILE", raising=False)
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: captured.update(kwargs))

    with caplog.at_level(logging.WARNING, logger="lemonade_a2a"):
        server.main()

    assert captured["host"] == "0.0.0.0"
    assert captured["ssl_certfile"] is None
    assert "without LEMONADE_A2A_API_KEY" in caplog.text
    assert "without TLS" in caplog.text


def test_main_is_quiet_on_loopback(monkeypatch, caplog) -> None:
    monkeypatch.setenv("LEMONADE_A2A_HOST", "127.0.0.1")
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: None)

    with caplog.at_level(logging.WARNING, logger="lemonade_a2a"):
        server.main()

    assert "without" not in caplog.text


def test_main_forwards_tls_files(monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv("LEMONADE_A2A_SSL_CERTFILE", "cert.pem")
    monkeypatch.setenv("LEMONADE_A2A_SSL_KEYFILE", "key.pem")
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: captured.update(kwargs))

    server.main()

    assert (captured["ssl_certfile"], captured["ssl_keyfile"]) == ("cert.pem", "key.pem")


def test_version_falls_back_when_package_metadata_is_missing(monkeypatch) -> None:
    def missing(_name):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing)
    try:
        importlib.reload(lemonade_a2a)
        assert lemonade_a2a.__version__ == "0.0.0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(lemonade_a2a)


def test_app_lifespan_closes_the_backend_client() -> None:
    with TestClient(create_app(Settings())) as client:
        assert client.get("/healthz").status_code == 200


@pytest.mark.asyncio
async def test_non_streaming_chat_returns_message_content(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi there"}}]})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: original(*a, **{**k, "transport": transport})
    )
    client = LemonadeClient("http://localhost:13305/v1", "m")

    assert await client.chat([{"role": "user", "content": "hi"}]) == "hi there"
    await client.aclose()
