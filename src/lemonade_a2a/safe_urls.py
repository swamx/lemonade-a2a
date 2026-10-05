"""Policy for URLs the adapter might one day fetch on a caller's behalf.

Nothing fetches remote content today: file/URL/data parts are rejected, and push
notifications are not offered (see docs/security.md). This module is the checked
policy that must gate either feature before it is enabled, so a caller-supplied URL
cannot make the adapter reach loopback, the LAN or a cloud metadata endpoint (SSRF).

The check resolves the host and requires *every* resolved address to be globally
routable. A caller that then connects must connect to one of the addresses returned
here, not resolve the name again, or DNS rebinding can swap in a private address.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
_DNS_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_INTERNAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")

Resolver = Callable[[str, int], list[str]]


class UnsafeURL(ValueError):
    """The URL is not acceptable for a server-side fetch."""


def system_resolver(host: str, port: int) -> list[str]:
    try:
        return sorted(
            {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        )
    except socket.gaierror as exc:
        raise UnsafeURL(f"host does not resolve: {host}") from exc


def _check_address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(text.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped  # ::ffff:127.0.0.1 is loopback, not a public IPv6 host
    if not address.is_global or address.is_multicast:
        raise UnsafeURL(f"address is not publicly routable: {address}")
    return address


def check_remote_url(
    url: str, *, allow_http: bool = False, resolver: Resolver = system_resolver
) -> tuple[str, list[str]]:
    """Return ``(url, vetted addresses)`` or raise :class:`UnsafeURL`."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise UnsafeURL("URL is empty or too long")
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url):
        raise UnsafeURL("URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL("URL is malformed") from exc

    allowed = {"https", "http"} if allow_http else {"https"}
    if parts.scheme.lower() not in allowed:
        raise UnsafeURL(f"scheme must be {' or '.join(sorted(allowed))}")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURL("credentials in the URL are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("URL has no host")
    port = port or (443 if parts.scheme.lower() == "https" else 80)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return url, [str(_check_address(host))]

    # Only real DNS names go to the resolver: the system resolver would also accept
    # "2130706433", "0x7f.1" or "0177.0.0.1" and hand back 127.0.0.1.
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURL("host is not a valid name") from exc
    if not _DNS_NAME.match(ascii_host) or ascii_host.endswith(_INTERNAL_SUFFIXES):
        raise UnsafeURL("host is not a public DNS name")

    resolved = resolver(ascii_host, port)
    if not resolved:
        raise UnsafeURL(f"host does not resolve: {host}")
    return url, [str(_check_address(address)) for address in resolved]
