"""Reusable discovery abstractions; remote registries can implement resolve()."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class SSRFError(ValueError):
    """A remote agent host failed SSRF validation (private/internal target)."""


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
            raise SSRFError("Private or local agent URLs are not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None  # Not a literal IP (e.g. a domain name); DNS is resolved later.
        if address is not None and not _is_global(address):
            raise SSRFError("Non-public IP addresses are not allowed")
    return url


def _is_global(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject public-but-sensitive ranges that `is_global` alone lets through.

    `is_global` excludes private/loopback/link-local ranges but not every
    cloud metadata or carrier-grade-NAT range (e.g. 169.254.169.254, used by
    AWS/GCP/Azure instance metadata services, is link-local and so already
    excluded, but IPv4-mapped IPv6 and NAT64 addresses need unwrapping first).
    """
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped or address.sixtofour or address.teredo
        if mapped is not None:
            unwrapped = mapped[0] if isinstance(mapped, tuple) else mapped
            return _is_global(ipaddress.ip_address(unwrapped))
    return address.is_global


async def resolve_pinned_address(hostname: str, *, allow_local: bool = False) -> str:
    """Resolve `hostname` once and return a single validated IP literal to connect to.

    Resolving exactly once and pinning the connection to that address (rather
    than letting the HTTP stack re-resolve DNS at connect time) closes the
    classic SSRF-via-DNS-rebinding gap: a hostname that resolves to a public
    address during validation and to a private/internal address moments later
    at connection time.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SSRFError(f"Could not resolve agent host: {hostname}") from exc
    if not infos:
        raise SSRFError(f"Could not resolve agent host: {hostname}")
    resolved = infos[0][4][0].split("%", 1)[0]  # strip IPv6 zone id, if any
    if not allow_local:
        address = ipaddress.ip_address(resolved)
        if not _is_global(address):
            raise SSRFError(f"Agent host resolves to a non-public address: {resolved}")
    return resolved


class StaticDiscovery:
    """Explicit trusted alias -> A2A base URL mapping (not network lookup)."""

    def __init__(self, agents: dict[str, AgentLocation]) -> None:
        self._agents = dict(agents)

    async def resolve(self, alias: str) -> AgentLocation:
        try:
            return self._agents[alias]
        except KeyError as exc:
            raise LookupError(f"Unknown agent alias: {alias}") from exc
