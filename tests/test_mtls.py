"""Mutual TLS: the listener refuses clients that do not present a certificate from our CA."""

from __future__ import annotations

import datetime
import ipaddress
import ssl
from pathlib import Path

import httpx
import pytest

pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from lemonade_a2a.config import Settings
from lemonade_a2a.server import create_app, uvicorn_options
from tests.live import free_port, serve


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _issue(common_name, key, issuer_name, issuer_key, *, ca=False, usage=None, san=None):
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=not ca,
                key_cert_sign=ca,
                crl_sign=ca,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    )
    if usage:
        builder = builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def _write(path: Path, cert=None, key=None) -> str:
    if cert is not None:
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    if key is not None:
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return str(path)


def _pki(directory: Path, prefix: str = ""):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _issue(f"{prefix}test-ca", ca_key, _name(f"{prefix}test-ca"), ca_key, ca=True)
    paths = {"ca": _write(directory / f"{prefix}ca.pem", ca)}
    for role, usage, san in (
        (
            "server",
            ExtendedKeyUsageOID.SERVER_AUTH,
            [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))],
        ),
        ("client", ExtendedKeyUsageOID.CLIENT_AUTH, None),
    ):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = _issue(f"{prefix}{role}", key, ca.subject, ca_key, usage=usage, san=san)
        paths[role] = _write(directory / f"{prefix}{role}.pem", cert)
        paths[f"{role}_key"] = _write(directory / f"{prefix}{role}.key", key=key)
    return paths


def _tls(ca: str, client: tuple[str, str] | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca)
    if client:
        context.load_cert_chain(*client)
    return context


@pytest.fixture
def mtls_server(tmp_path):
    pki = _pki(tmp_path)
    port = free_port()
    settings = Settings(
        port=port,
        public_url=f"https://127.0.0.1:{port}",
        ssl_certfile=pki["server"],
        ssl_keyfile=pki["server_key"],
        ssl_ca_certs=pki["ca"],
        ssl_require_client_cert=True,
        profile="lan",
        host="127.0.0.1",
        api_key="k",
    )
    options = uvicorn_options(settings)
    options.pop("host"), options.pop("port")
    with serve(create_app(settings), port, **options):
        yield f"https://127.0.0.1:{port}", pki, tmp_path


def test_options_demand_a_client_certificate() -> None:
    settings = Settings(
        ssl_certfile="s.pem",
        ssl_keyfile="s.key",
        ssl_ca_certs="ca.pem",
        ssl_require_client_cert=True,
    )

    options = uvicorn_options(settings)

    assert options["ssl_cert_reqs"] == ssl.CERT_REQUIRED
    assert options["ssl_ca_certs"] == "ca.pem"
    assert "ssl_cert_reqs" not in uvicorn_options(Settings())


def test_client_certificates_need_a_ca_and_a_server_certificate() -> None:
    with pytest.raises(ValueError, match="CA"):
        Settings(ssl_require_client_cert=True)
    with pytest.raises(ValueError, match="CA"):
        Settings(ssl_certfile="s", ssl_keyfile="k", ssl_require_client_cert=True)


def test_a_client_with_a_certificate_from_our_ca_is_served(mtls_server) -> None:
    base, pki, _ = mtls_server

    with httpx.Client(verify=_tls(pki["ca"], (pki["client"], pki["client_key"]))) as client:
        assert client.get(f"{base}/healthz").json() == {"status": "ok"}
        card = client.get(f"{base}/.well-known/agent-card.json")
        assert card.status_code == 200


def test_a_client_without_a_certificate_cannot_connect(mtls_server) -> None:
    base, pki, _ = mtls_server

    with pytest.raises(httpx.HTTPError), httpx.Client(verify=_tls(pki["ca"])) as client:
        client.get(f"{base}/healthz")


def test_a_certificate_from_another_ca_is_refused(mtls_server) -> None:
    base, pki, directory = mtls_server
    stranger = _pki(directory, prefix="other-")

    with (
        pytest.raises(httpx.HTTPError),
        httpx.Client(
            verify=_tls(pki["ca"], (stranger["client"], stranger["client_key"]))
        ) as client,
    ):
        client.get(f"{base}/healthz")


def test_mtls_does_not_replace_the_api_key(mtls_server) -> None:
    """The profile still demands credentials; a valid certificate is transport access only."""
    base, pki, _ = mtls_server
    headers = {"A2A-Version": "1.0"}

    with httpx.Client(verify=_tls(pki["ca"], (pki["client"], pki["client_key"]))) as client:
        assert client.get(f"{base}/tasks", headers=headers).status_code == 401
        assert (
            client.get(
                f"{base}/tasks", headers={**headers, "Authorization": "Bearer k"}
            ).status_code
            == 200
        )
