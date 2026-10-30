"""Boundary regression tests: disallowed DNS targets and untrusted Agent Cards."""

import asyncio

import httpx
import pytest

from lemonade_a2a.discovery import SSRFError, resolve_pinned_address, validate_remote_url
from lemonade_a2a.discovery.cards import fetch_agent_card


@pytest.mark.asyncio
async def test_mixed_dns_answers_rejected(monkeypatch):
    loop = asyncio.get_running_loop()

    async def dns(host, port, *, type):
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", dns)
    with pytest.raises(SSRFError, match="non-public"):
        await resolve_pinned_address("agent.example")


def test_query_string_in_agent_url_forbidden():
    with pytest.raises(ValueError):
        validate_remote_url("https://agent.example/a2a?token=secret")


@pytest.mark.asyncio
async def test_card_fetch_enforces_allowlist_before_network():
    with pytest.raises(ValueError, match="allowlist"):
        await fetch_agent_card(
            "https://unapproved.example/.well-known/agent-card.json",
            allowed_hosts=frozenset({"approved.example"}),
        )


@pytest.mark.asyncio
async def test_card_fetch_rejects_unapproved_advertised_interface(monkeypatch):
    async def fake_get(self, url):
        return httpx.Response(
            200,
            json={
                "name": "demo",
                "supportedInterfaces": [
                    {"url": "https://another.example/a2a", "protocolBinding": "JSONRPC"}
                ],
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(ValueError, match="Advertised interface"):
        await fetch_agent_card(
            "https://approved.example/.well-known/agent-card.json",
            allowed_hosts=frozenset({"approved.example"}),
        )


@pytest.mark.asyncio
async def test_card_fetch_rejects_redirect(monkeypatch):
    async def fake_get(self, url):
        return httpx.Response(
            302,
            headers={"location": "https://elsewhere.example"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(ValueError, match="redirect"):
        await fetch_agent_card(
            "https://approved.example/.well-known/agent-card.json",
            allowed_hosts=frozenset({"approved.example"}),
        )
