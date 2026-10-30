"""Trusted Agent Card retrieval using the same guarded transport as A2A traffic."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from lemonade_a2a.client._ssrf_transport import SSRFGuardedTransport
from lemonade_a2a.discovery import validate_remote_url


async def fetch_agent_card(
    card_url: str,
    *,
    allowed_hosts: frozenset[str] | None = None,
    timeout: float = 20.0,
    allow_local: bool = False,
) -> dict:
    """Fetch and minimally validate a card without unguarded network requests."""
    validate_remote_url(card_url, allow_local=allow_local)
    origin = urlsplit(card_url)
    if allowed_hosts is not None and origin.hostname not in allowed_hosts:
        raise ValueError("Agent Card hostname is not on the approved allowlist")
    transport = SSRFGuardedTransport(allow_local=allow_local)
    async with httpx.AsyncClient(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        response = await client.get(card_url)
        if response.is_redirect:
            raise ValueError("Agent Card redirects are forbidden")
        response.raise_for_status()
        if len(response.content) > 1024 * 1024:
            raise ValueError("Agent Card exceeds size limit")
        card = response.json()
    if not isinstance(card, dict) or not isinstance(card.get("name"), str):
        raise ValueError("Invalid Agent Card")
    interfaces = card.get("supportedInterfaces", [])
    if interfaces and not isinstance(interfaces, list):
        raise ValueError("Invalid Agent Card interfaces")
    for interface in interfaces:
        if not isinstance(interface, dict) or not isinstance(interface.get("url"), str):
            raise ValueError("Invalid Agent Card interface")
        target = interface["url"]
        validate_remote_url(target, allow_local=allow_local)
        endpoint = urlsplit(target)
        if allowed_hosts is not None and endpoint.hostname not in allowed_hosts:
            raise ValueError("Advertised interface is not on the approved allowlist")
    return card
