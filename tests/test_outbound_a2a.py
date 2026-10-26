"""Tests for the outbound A2A wire format and failure handling."""
import httpx
import pytest

from lemonade_a2a.outbound import RemoteA2AClient, RemoteA2AError


def test_remote_endpoint_must_be_https():
    with pytest.raises(ValueError):
        RemoteA2AClient("http://localhost:9100")


@pytest.mark.asyncio
async def test_sends_a2a_03_data_part(monkeypatch):
    captured = {}

    async def fake_post(self, endpoint, *, json):
        captured.update({"endpoint": endpoint, "body": json})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": json["id"], "result": {"kind": "task", "id": "t1"}},
            request=httpx.Request("POST", endpoint),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await RemoteA2AClient("https://example.org/a2a").send_data(
        "search_flights", {"origin": "MAD", "destination": "LHR", "departure_date": "2026-11-19"}
    )
    assert result["id"] == "t1"
    assert captured["body"]["method"] == "message/send"
    assert captured["body"]["params"]["message"]["role"] == "user"
    assert captured["body"]["params"]["message"]["parts"][0]["data"]["action"] == "search_flights"


@pytest.mark.asyncio
async def test_rejects_mismatched_rpc_id(monkeypatch):
    async def fake_post(self, endpoint, *, json):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "wrong", "result": {}},
                              request=httpx.Request("POST", endpoint))
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(RemoteA2AError, match="identifier mismatch"):
        await RemoteA2AClient("https://example.org/a2a").send_data("search_flights", {})


@pytest.mark.asyncio
async def test_send_message_accepts_protocol_parts(monkeypatch):
    captured = {}

    async def fake_post(self, endpoint, *, json):
        captured["parts"] = json["params"]["message"]["parts"]
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": json["id"], "result": {"kind": "message"}},
            request=httpx.Request("POST", endpoint),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = RemoteA2AClient("https://example.org/a2a")
    await client.send_message([{"kind": "text", "text": "Hello remote agent"}])
    assert captured["parts"] == [{"kind": "text", "text": "Hello remote agent"}]


@pytest.mark.asyncio
async def test_send_message_rejects_missing_parts():
    client = RemoteA2AClient("https://example.org/a2a")
    with pytest.raises(ValueError, match="message part"):
        await client.send_message([])
