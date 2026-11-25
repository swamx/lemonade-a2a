"""Shared SSRF-guarded httpx transport for outbound A2A clients.

`discovery.validate_remote_url` only catches a literal IP/hostname in the
configured base URL at construction time. A hostname that resolves to a
public address then and to a private/internal one at actual connect time
(DNS rebinding) would otherwise sail straight past that check. This
transport resolves the host exactly once per request, checks *every* answer,
and connects only to those checked addresses instead of letting the HTTP
stack re-resolve DNS later.

Addresses are tried in resolver order and the next one is used only when a
connection cannot be made (refused or unreachable). This matters for
``localhost``, which commonly resolves to ``::1`` first while a server listens
on ``127.0.0.1`` only.
"""

from __future__ import annotations

from typing import Any

import httpx

from lemonade_a2a.discovery import resolve_pinned_addresses


class SSRFGuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(
        self, *, allow_local: bool, allowed_hosts: frozenset[str] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._allow_local = allow_local
        self._allowed_hosts = allowed_hosts

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if self._allowed_hosts is not None and host.lower() not in self._allowed_hosts:
            raise ValueError("Outbound hostname is not on the approved allowlist")
        addresses = await resolve_pinned_addresses(host, allow_local=self._allow_local)
        failure: httpx.ConnectError | None = None
        for pinned_ip in addresses:
            pinned_request = httpx.Request(
                request.method,
                request.url.copy_with(host=pinned_ip),
                headers=request.headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": host},
            )
            try:
                return await super().handle_async_request(pinned_request)
            except httpx.ConnectError as exc:  # nothing was sent: safe to try the next one
                failure = exc
        assert failure is not None  # resolve_pinned_addresses never returns an empty list
        raise failure
