"""Tests for the SSRF-hardening DNS resolution and transport pinning."""

import asyncio
import ipaddress

import httpx
import pytest

from lemonade_a2a.client._ssrf_transport import SSRFGuardedTransport
from lemonade_a2a.discovery import SSRFError, _is_global, resolve_pinned_address


@pytest.mark.asyncio
async def test_resolve_pinned_address_rejects_private_target(monkeypatch):
    loop = asyncio.get_event_loop()

    async def fake_getaddrinfo(host, port, *, type):
        return [(None, None, None, None, ("10.0.0.5", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError, match="non-public address"):
        await resolve_pinned_address("internal.example")


@pytest.mark.asyncio
async def test_resolve_pinned_address_rejects_if_any_answer_is_private(monkeypatch):
    """Fail closed: a mix of public and private answers must still be rejected.

    Picking the first "good" address and ignoring a private one elsewhere in
    the answer list would let an attacker who controls one DNS answer (e.g.
    via a compromised resolver or a multi-A-record rebinding setup) hide a
    private/internal target behind a public decoy address.
    """
    loop = asyncio.get_event_loop()

    async def fake_getaddrinfo(host, port, *, type):
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError, match="non-public address"):
        await resolve_pinned_address("mixed.example")


@pytest.mark.asyncio
async def test_resolve_pinned_address_rejects_invalid_answer(monkeypatch):
    loop = asyncio.get_event_loop()

    async def fake_getaddrinfo(host, port, *, type):
        return [(None, None, None, None, ("not-an-ip", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError, match="invalid address"):
        await resolve_pinned_address("broken.example")


@pytest.mark.asyncio
async def test_resolve_pinned_address_allows_public_target(monkeypatch):
    loop = asyncio.get_event_loop()

    async def fake_getaddrinfo(host, port, *, type):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    assert await resolve_pinned_address("public.example") == "93.184.216.34"


@pytest.mark.asyncio
async def test_resolve_pinned_address_allow_local_bypasses_check(monkeypatch):
    loop = asyncio.get_event_loop()

    async def fake_getaddrinfo(host, port, *, type):
        return [(None, None, None, None, ("127.0.0.1", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    assert await resolve_pinned_address("localhost", allow_local=True) == "127.0.0.1"


@pytest.mark.asyncio
async def test_resolve_pinned_address_dns_failure_raises_ssrf_error(monkeypatch):
    loop = asyncio.get_event_loop()

    async def fake_getaddrinfo(host, port, *, type):
        raise OSError("name resolution failed")

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError, match="Could not resolve"):
        await resolve_pinned_address("nonexistent.invalid")


def test_is_global_unwraps_ipv4_mapped_ipv6():
    # ::ffff:169.254.169.254 is cloud instance-metadata, not public, even
    # though the bare IPv6Address.is_global check alone can be fooled by
    # the wrapping.
    metadata_mapped = ipaddress.ip_address("::ffff:169.254.169.254")
    assert not _is_global(metadata_mapped)

    public_mapped = ipaddress.ip_address("::ffff:93.184.216.34")
    assert _is_global(public_mapped)


@pytest.mark.asyncio
async def test_transport_pins_connection_and_rejects_rebinding(monkeypatch):
    """Simulates DNS rebinding: resolution flips to a private address.

    The transport must resolve exactly once per request and reject before
    ever opening a socket, rather than connecting to whatever a second,
    later DNS lookup inside the HTTP stack would return.
    """
    calls = {"n": 0}

    async def flipping_getaddrinfo(host, port, *, type):
        calls["n"] += 1
        # First call (used for validation) is private; must be rejected.
        return [(None, None, None, None, ("169.254.169.254", 0))]

    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "getaddrinfo", flipping_getaddrinfo)

    transport = SSRFGuardedTransport(allow_local=False)
    request = httpx.Request("GET", "https://rebinding.example/a2a")
    with pytest.raises(SSRFError):
        await transport.handle_async_request(request)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_transport_preserves_host_header_and_sni_when_pinned(monkeypatch):
    seen = {}

    class RecordingPool(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            seen["url"] = request.url
            seen["host_header"] = request.headers.get("host")
            seen["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, request=request)

    loop = asyncio.get_event_loop()

    async def fake_getaddrinfo(host, port, *, type):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)

    transport = SSRFGuardedTransport(allow_local=False)
    # Swap in a fake inner pool to observe what the base transport would send.
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", RecordingPool.handle_async_request
    )
    request = httpx.Request("GET", "https://pinned.example/a2a")
    await transport.handle_async_request(request)
    assert seen["url"].host == "93.184.216.34"
    assert seen["host_header"] == "pinned.example"
    assert seen["sni"] == "pinned.example"
