"""Reusable discovery abstractions; remote registries can implement resolve()."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AgentLocation:
    base_url: str
    card_path: str = "/.well-known/agent-card.json"


def validate_remote_url(url: str, *, allow_local: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in (("https", "http") if allow_local else ("https",)):
        raise ValueError("Remote URLs must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Invalid remote agent URL")
    host = parsed.hostname.lower()
    if not allow_local:
        if host == "localhost" or host.endswith(".local"):
            raise ValueError("Private or local agent URLs are not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None  # Not a literal IP (e.g. a domain name); DNS is resolved later.
        if address is not None and not address.is_global:
            raise ValueError("Non-public IP addresses are not allowed")
    return url


class StaticDiscovery:
    """Explicit trusted alias -> A2A base URL mapping (not network lookup)."""

    def __init__(self, agents: dict[str, AgentLocation]) -> None:
        self._agents = dict(agents)

    async def resolve(self, alias: str) -> AgentLocation:
        try:
            return self._agents[alias]
        except KeyError as exc:
            raise LookupError(f"Unknown agent alias: {alias}") from exc
