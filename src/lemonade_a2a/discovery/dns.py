"""Find a marketplace (or a single agent) from DNS SRV records, as with other services.

For a domain ``example.com`` two service names are looked up:

``_a2a-registry._tcp.example.com``  SRV -> hosts serving an agent marketplace listing
``_a2a._tcp.example.com``           SRV -> hosts serving one agent's Agent Card

Each SRV answer gives ``priority weight port target`` (RFC 2782); targets are tried by
ascending priority, then descending weight. An optional TXT record on the same service
name carries ``key=value`` strings (as in DNS-SD, RFC 6763): ``path=/api/agents`` names the
listing (or card) path on the target; without it the well-known paths are used.

DNS answers are untrusted like everything else: the URL built from them goes through the
same URL policy and SSRF-guarded transport as a link typed by hand, so a record cannot point
the adapter at a private address. ``.`` as target ("service not offered") is ignored.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger("lemonade_a2a.dns")

REGISTRY_SERVICE = "_a2a-registry._tcp"
AGENT_SERVICE = "_a2a._tcp"
MAX_RECORDS = 10
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,255}$")

# (name, "SRV" | "TXT") -> SRV: [(priority, weight, port, target)], TXT: [text, ...]
Resolver = Callable[[str, str], Awaitable[list[Any]]]


@dataclass(frozen=True)
class ServiceTarget:
    priority: int
    weight: int
    port: int
    host: str
    path: str = ""  # from TXT path=..., empty for the well-known paths

    def origin(self, scheme: str) -> str:
        default = 443 if scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{scheme}://{host}" + ("" if self.port == default else f":{self.port}")


async def system_resolver(name: str, rdtype: str, *, timeout: float = 5.0) -> list[Any]:
    """Query DNS with dnspython; a name without such records gives ``[]``."""
    try:
        import dns.asyncresolver
        import dns.exception
        import dns.resolver
    except ImportError:  # pragma: no cover - dnspython is a dependency
        LOG.debug("dnspython is not installed; DNS SRV discovery is off")
        return []
    try:
        answer = await dns.asyncresolver.resolve(name, rdtype, lifetime=timeout)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException as exc:
        LOG.debug("DNS %s lookup for %s failed: %s", rdtype, name, exc)
        return []
    if rdtype == "SRV":
        return [(r.priority, r.weight, r.port, r.target.to_text()) for r in answer]
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


def txt_settings(records: list[Any]) -> dict[str, str]:
    """``key=value`` pairs from TXT strings (first value wins, keys lower-cased)."""
    settings: dict[str, str] = {}
    for record in records:
        if not isinstance(record, str) or "=" not in record:
            continue
        key, value = record.split("=", 1)
        settings.setdefault(key.strip().lower(), value.strip())
    return settings


async def lookup_service(
    domain: str, service: str, resolver: Resolver | None = None
) -> list[ServiceTarget]:
    """SRV targets for ``<service>.<domain>``, in the order they should be tried."""
    resolve = resolver or system_resolver
    name = f"{service}.{domain.strip('.')}"
    records = (await resolve(name, "SRV"))[:MAX_RECORDS]
    if not records:
        return []
    path = txt_settings(await resolve(name, "TXT")).get("path", "")
    if path and (not _PATH.match(path) or ".." in path):
        LOG.warning("ignoring TXT path for %s: not a plain absolute path", name)
        path = ""
    targets = []
    for record in records:
        try:
            priority, weight, port, host = record
            priority, weight, port = int(priority), int(weight), int(port)
        except (TypeError, ValueError):
            continue
        host = str(host).rstrip(".").lower()
        if not host or not 0 < port < 65536:
            continue  # "." means the service is not offered
        targets.append(ServiceTarget(priority, weight, port, host, path))
    return sorted(targets, key=lambda t: (t.priority, -t.weight))
