"""Contract tests for SDK facade, using a fake SDK (no network)."""

from unittest.mock import AsyncMock

import pytest

from lemonade_a2a.client import A2AClient
from lemonade_a2a.discovery import AgentLocation, StaticDiscovery, validate_remote_url


def test_discovery_url_guards():
    assert validate_remote_url("https://agent.example/a2a")
    with pytest.raises(ValueError):
        validate_remote_url("http://agent.example")
    with pytest.raises(ValueError):
        validate_remote_url("https://127.0.0.1/a2a")
    with pytest.raises(ValueError):
        validate_remote_url("https://localhost/a2a")
    assert validate_remote_url("http://127.0.0.1:9100", allow_local=True)


@pytest.mark.asyncio
async def test_static_registry():
    registry = StaticDiscovery({"flight": AgentLocation("https://agent.example")})
    assert (await registry.resolve("flight")).base_url == "https://agent.example"
    with pytest.raises(LookupError):
        await registry.resolve("missing")


@pytest.mark.asyncio
async def test_sdk_delegation_and_close(monkeypatch):
    seen = {}

    class FakeSDK:
        close = AsyncMock()

        async def send_message(self, request):
            yield {"request": request}

        async def get_task(self, request):
            return {"task": request}

        async def cancel_task(self, request):
            return {"cancel": request}

    sdk = FakeSDK()

    class FakeFactory:
        def __init__(self, config):
            seen["config"] = config

        async def create_from_url(self, url):
            seen["url"] = url
            return sdk

    monkeypatch.setattr("lemonade_a2a.client.sdk.ClientFactory", FakeFactory)
    async with A2AClient("https://agent.example", bearer_token="secret") as client:
        assert await client.send_and_collect("hello") == [{"request": "hello"}]
        assert await client.get_task("task") == {"task": "task"}
        assert await client.cancel_task("task") == {"cancel": "task"}
        assert seen["url"] == "https://agent.example"
    sdk.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_aclose_still_closes_http_when_sdk_close_raises(monkeypatch):
    class FailingSDK:
        async def close(self):
            raise RuntimeError("sdk close failed")

    class FakeFactory:
        def __init__(self, config):
            pass

        async def create_from_url(self, url):
            return FailingSDK()

    monkeypatch.setattr("lemonade_a2a.client.sdk.ClientFactory", FakeFactory)
    client = await A2AClient("https://agent.example").__aenter__()
    http = client._http
    with pytest.raises(RuntimeError):
        await client.aclose()
    assert http.is_closed


@pytest.mark.asyncio
async def test_get_task_retries_transient_read_only(monkeypatch):
    import httpx

    calls = 0

    class FakeSDK:
        async def close(self):
            pass

        async def get_task(self, request):
            nonlocal calls
            calls += 1
            if calls < 2:
                raise httpx.ConnectError("temporary connection problem")
            return {"status": "completed"}

    class FakeFactory:
        def __init__(self, config):
            pass

        async def create_from_url(self, url):
            return FakeSDK()

    async def no_delay(_):
        return None

    monkeypatch.setattr("lemonade_a2a.client.sdk.ClientFactory", FakeFactory)
    monkeypatch.setattr("lemonade_a2a.client.sdk.asyncio.sleep", no_delay)
    async with A2AClient("https://agent.example") as client:
        assert await client.get_task("task", retries=1) == {"status": "completed"}
        assert calls == 2
        with pytest.raises(ValueError):
            await client.get_task("task", retries=4)
