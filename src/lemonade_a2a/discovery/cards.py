"""Trusted Agent Card retrieval using the same guarded transport as A2A traffic."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from lemonade_a2a.client._ssrf_transport import SSRFGuardedTransport
from lemonade_a2a.discovery import validate_remote_url

MAX_CARD_BYTES = 1024 * 1024


def _check_host(url: str, allowed_hosts: frozenset[str] | None, what: str) -> None:
    host = (urlsplit(url).hostname or "").lower()
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValueError(f"{what} is not on the approved allowlist")


def validate_agent_card(
    card: Any,
    *,
    allowed_hosts: frozenset[str] | None = None,
    allow_local: bool = False,
) -> dict:
    """Minimal structural and endpoint checks on a card from any untrusted source.

    Every endpoint the card advertises (the 0.3 top-level ``url`` and each 1.x
    ``supportedInterfaces`` entry) must pass the same URL policy as the card itself,
    so a card cannot steer a client towards a private or unapproved host.
    """
    if not isinstance(card, dict) or not isinstance(card.get("name"), str):
        raise TypeError("Invalid Agent Card")
    # A2A 0.3 cards commonly advertise the primary endpoint as top-level url.
    primary_url = card.get("url")
    if primary_url is not None:
        if not isinstance(primary_url, str):
            raise TypeError("Invalid Agent Card URL")
        validate_remote_url(primary_url, allow_local=allow_local)
        _check_host(primary_url, allowed_hosts, "Advertised endpoint")
    interfaces = card.get("supportedInterfaces", [])
    if interfaces and not isinstance(interfaces, list):
        raise ValueError("Invalid Agent Card interfaces")
    for interface in interfaces:
        if not isinstance(interface, dict) or not isinstance(interface.get("url"), str):
            raise TypeError("Invalid Agent Card interface")
        validate_remote_url(interface["url"], allow_local=allow_local)
        _check_host(interface["url"], allowed_hosts, "Advertised interface")
    if primary_url is None and not interfaces:
        raise ValueError("Agent Card advertises no endpoint")
    return card


async def fetch_agent_card(
    card_url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
    timeout: float = 20.0,
    allow_local: bool = False,
) -> dict:
    """Fetch and minimally validate a card without unguarded network requests."""
    validate_remote_url(card_url, allow_local=allow_local)
    _check_host(card_url, allowed_hosts, "Agent Card hostname")
    transport = SSRFGuardedTransport(allow_local=allow_local, allowed_hosts=allowed_hosts)
    async with httpx.AsyncClient(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        response = await client.get(card_url)
        if response.is_redirect:
            raise ValueError("Agent Card redirects are forbidden")
        response.raise_for_status()
        if len(response.content) > MAX_CARD_BYTES:
            raise ValueError("Agent Card exceeds size limit")
        card = response.json()
    return validate_agent_card(card, allowed_hosts=allowed_hosts, allow_local=allow_local)
