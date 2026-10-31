"""Real-TLS tests for `SSRFGuardedTransport`.

`tests/test_ssrf_guard.py` already proves that the transport rewrites the
outbound request's URL to the pinned IP while keeping the `Host` header and
`sni_hostname` extension set to the original hostname -- but it does so
against a *mocked* inner transport, so it never actually performs a TLS
handshake. These tests spin up real local HTTPS servers (self-signed certs
generated with `cryptography`, already a project test dependency) and drive
genuine `httpx.AsyncClient` + `SSRFGuardedTransport` requests through them, to
prove that:

1. Pinning to an IP does not weaken hostname verification: a handshake
   against a cert issued for the original hostname succeeds (SNI/hostname
   checking uses the *original* host, not the IP literal).
2. A cert issued for a different hostname is rejected -- pinning does not
   bypass verification.
3. Both IPv4 and IPv6 loopback addresses work through the pinning path.
4. A real 302 response is returned as-is (not auto-followed), matching the
   `follow_redirects=False` configuration used everywhere in the project.
"""

from __future__ import annotations

import asyncio
import datetime
import socket
import ssl
from typing import Self

import httpx
import pytest

from lemonade_a2a.client._ssrf_transport import SSRFGuardedTransport

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    _HAS_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover - cryptography is a declared test dependency
    _HAS_CRYPTOGRAPHY = False

pytestmark = pytest.mark.skipif(
    not _HAS_CRYPTOGRAPHY,
    reason="cryptography package not available; cannot generate a self-signed test certificate",
)


def _generate_self_signed_cert(hostname: str) -> tuple[bytes, bytes]:
    """Return (cert_pem, key_pem) for a throwaway self-signed cert for `hostname`."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(minutes=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
    )
    cert = builder.sign(key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


class _TLSServer:
    """A minimal asyncio HTTPS server: reads one request, writes one canned response."""

    def __init__(self, cert_pem: bytes, key_pem: bytes, response: bytes, bind_host: str) -> None:
        self._ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        import tempfile
        from pathlib import Path

        tmp_dir = Path(tempfile.mkdtemp())
        cert_path = tmp_dir / "cert.pem"
        key_path = tmp_dir / "key.pem"
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)
        self._ssl_ctx.load_cert_chain(str(cert_path), str(key_path))
        self._response = response
        self._bind_host = bind_host
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(
            self._handle, host=self._bind_host, port=0, ssl=self._ssl_ctx
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # Drain the request (headers terminated by a blank line) before replying.
            while True:
                line = await reader.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
            writer.write(self._response)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()


def _patch_getaddrinfo(monkeypatch: pytest.MonkeyPatch, hostname: str, ip: str) -> None:
    loop = asyncio.get_event_loop()
    real_getaddrinfo = loop.getaddrinfo

    async def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == hostname:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
        return await real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)


def _client_ssl_context(cert_pem: bytes) -> ssl.SSLContext:
    """A client context that trusts only the given self-signed cert (real verification)."""
    import tempfile
    from pathlib import Path

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tmp_dir = Path(tempfile.mkdtemp())
    ca_path = tmp_dir / "ca.pem"
    ca_path.write_bytes(cert_pem)
    ctx.load_verify_locations(str(ca_path))
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


_OK_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
_REDIRECT_RESPONSE = (
    b"HTTP/1.1 302 Found\r\nLocation: https://example.invalid/elsewhere\r\n"
    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
)


@pytest.mark.asyncio
async def test_pinned_connection_passes_real_tls_verification_ipv4(monkeypatch):
    """Cert issued for the original hostname + SNI preserved -> handshake succeeds."""
    hostname = "pinned-tls-test.invalid"
    cert_pem, key_pem = _generate_self_signed_cert(hostname)
    _patch_getaddrinfo(monkeypatch, hostname, "127.0.0.1")

    async with _TLSServer(cert_pem, key_pem, _OK_RESPONSE, "127.0.0.1") as server:
        client_ctx = _client_ssl_context(cert_pem)
        transport = SSRFGuardedTransport(allow_local=True, verify=client_ctx)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            response = await client.get(f"https://{hostname}:{server.port}/")
            assert response.status_code == 200
            assert response.text == "ok"


@pytest.mark.asyncio
async def test_pinned_connection_rejects_mismatched_certificate(monkeypatch):
    """Pinning to a reachable IP whose cert doesn't match the hostname must still fail TLS."""
    hostname = "pinned-tls-test.invalid"
    wrong_hostname = "someone-else.invalid"
    cert_pem, key_pem = _generate_self_signed_cert(wrong_hostname)
    _patch_getaddrinfo(monkeypatch, hostname, "127.0.0.1")

    async with _TLSServer(cert_pem, key_pem, _OK_RESPONSE, "127.0.0.1") as server:
        client_ctx = _client_ssl_context(cert_pem)  # trusts this CA, but wrong name
        transport = SSRFGuardedTransport(allow_local=True, verify=client_ctx)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            with pytest.raises(httpx.ConnectError) as exc_info:
                await client.get(f"https://{hostname}:{server.port}/")
            assert (
                "certificate" in str(exc_info.value).lower()
                or "hostname" in str(exc_info.value).lower()
            )


@pytest.mark.asyncio
async def test_pinned_connection_passes_real_tls_verification_ipv6(monkeypatch):
    """Same hostname-verification-under-pinning proof, but pinned to the IPv6 loopback."""
    if not socket.has_ipv6:
        pytest.skip("IPv6 not available in this environment")

    hostname = "pinned-tls-test-v6.invalid"
    cert_pem, key_pem = _generate_self_signed_cert(hostname)

    _patch_getaddrinfo(monkeypatch, hostname, "::1")

    try:
        async with _TLSServer(cert_pem, key_pem, _OK_RESPONSE, "::1") as server:
            client_ctx = _client_ssl_context(cert_pem)
            transport = SSRFGuardedTransport(allow_local=True, verify=client_ctx)
            async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
                response = await client.get(f"https://{hostname}:{server.port}/")
                assert response.status_code == 200
                assert response.text == "ok"
    except OSError:
        pytest.skip("IPv6 loopback (::1) is not bindable in this environment")


@pytest.mark.asyncio
async def test_redirect_is_not_auto_followed(monkeypatch):
    """A real 302 from the pinned server must come back as-is, not be auto-followed."""
    hostname = "pinned-tls-test.invalid"
    cert_pem, key_pem = _generate_self_signed_cert(hostname)
    _patch_getaddrinfo(monkeypatch, hostname, "127.0.0.1")

    async with _TLSServer(cert_pem, key_pem, _REDIRECT_RESPONSE, "127.0.0.1") as server:
        client_ctx = _client_ssl_context(cert_pem)
        transport = SSRFGuardedTransport(allow_local=True, verify=client_ctx)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            response = await client.get(f"https://{hostname}:{server.port}/")
            assert response.status_code == 302
            assert response.headers["location"] == "https://example.invalid/elsewhere"
