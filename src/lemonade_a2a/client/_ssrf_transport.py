"""Shared SSRF-guarded httpx transport for outbound A2A clients.

`discovery.validate_remote_url` only catches a literal IP/hostname in the
configured base URL at construction time. A hostname that resolves to a
public address then and to a private/internal one at actual connect time
(DNS rebinding) would otherwise sail straight past that check. This
transport resolves the host exactly once per request and connects to that
specific, validated address instead of letting the HTTP stack re-resolve
DNS later.
"""

from __future__ import annotations

from typing import Any

import httpx

from lemonade_a2a.discovery import resolve_pinned_address


class SSRFGuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, allow_local: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._allow_local = allow_local

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        pinned_ip = await resolve_pinned_address(host, allow_local=self._allow_local)
        pinned_request = httpx.Request(
            request.method,
            request.url.copy_with(host=pinned_ip),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return await super().handle_async_request(pinned_request)
