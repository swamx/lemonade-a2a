import json

import pytest
from fastapi.testclient import TestClient

from lemonade_a2a.config import Settings
from lemonade_a2a.safe_urls import UnsafeURL, check_remote_url
from lemonade_a2a.server import create_app

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


def public(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def test_a_public_https_url_is_accepted_with_its_vetted_addresses() -> None:
    url, addresses = check_remote_url("https://example.com/file.txt?a=1", resolver=public)

    assert url == "https://example.com/file.txt?a=1"
    assert addresses == ["93.184.216.34"]


def test_public_ip_literals_are_accepted() -> None:
    assert check_remote_url("https://93.184.216.34/x")[1] == ["93.184.216.34"]
    assert check_remote_url("https://[2606:2800:220:1:248:1893:25c8:1946]/x")[1]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x",  # plain http
        "ftp://example.com/x",
        "file:///etc/passwd",
        "data:text/plain;base64,aGk=",
        "javascript:alert(1)",
        "gopher://example.com/",
        "https://user:pw@example.com/x",  # credentials
        "https://user@example.com/x",
        "https:///nohost",
        "https://",
        "",
        "https://example.com/" + "a" * 3000,
        "https://exa mple.com/",
        "https://example.com/\nHost: evil",
        "https://example.com:99999/",
        # loopback, private, link-local, metadata, unspecified, CGNAT, multicast
        "https://127.0.0.1/",
        "https://127.1/",
        "https://localhost/",
        "https://0.0.0.0/",
        "https://10.1.2.3/",
        "https://172.16.0.1/",
        "https://192.168.1.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://100.64.0.1/",
        "https://224.0.0.1/",
        "https://[::1]/",
        "https://[fe80::1]/",
        "https://[fc00::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://[::ffff:10.0.0.1]/",
        # numeric host spellings the system resolver would turn into 127.0.0.1
        "https://2130706433/",
        "https://0x7f.1/",
        "https://0177.0.0.1/",
        # internal names
        "https://printer.local/",
        "https://db.internal/",
        "https://intranet/",
    ],
)
def test_unsafe_urls_are_refused(url: str) -> None:
    with pytest.raises(UnsafeURL):
        check_remote_url(url, resolver=public)


def test_a_name_that_resolves_to_a_private_address_is_refused() -> None:
    with pytest.raises(UnsafeURL, match="routable"):
        check_remote_url("https://rebind.example.com/", resolver=lambda h, p: ["10.0.0.5"])


def test_one_private_address_among_public_ones_refuses_the_url() -> None:
    mixed = lambda h, p: ["93.184.216.34", "127.0.0.1"]

    with pytest.raises(UnsafeURL):
        check_remote_url("https://example.com/", resolver=mixed)


def test_an_unresolvable_name_is_refused() -> None:
    with pytest.raises(UnsafeURL, match="resolve"):
        check_remote_url("https://example.com/", resolver=lambda h, p: [])


def test_http_can_be_allowed_explicitly() -> None:
    assert check_remote_url("http://example.com/x", allow_http=True, resolver=public)


def test_the_system_resolver_failure_is_an_unsafe_url_not_a_crash() -> None:
    with pytest.raises(UnsafeURL):
        check_remote_url("https://this-name-does-not-exist.invalid/")


# --- richer parts stay disabled until this policy is wired in --------------------------


@pytest.mark.parametrize(
    "part",
    [
        {"url": "https://example.com/doc.pdf", "mediaType": "application/pdf"},
        {"url": "http://169.254.169.254/latest/meta-data/"},
        {"url": "file:///etc/passwd"},
        {"raw": "aGVsbG8=", "mediaType": "text/plain", "filename": "../../x"},
        {"data": {"anything": [1, 2, 3]}},
    ],
)
def test_file_url_and_data_parts_are_refused_on_both_bindings(part: dict) -> None:
    client = TestClient(create_app(Settings()))
    message = {"messageId": "p1", "role": "ROLE_USER", "parts": [part]}

    rest = client.post("/message:send", headers=HEADERS, content=json.dumps({"message": message}))
    rpc = client.post(
        "/",
        headers=HEADERS,
        content=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": message}}
        ),
    )

    assert rest.status_code == 400
    assert rest.json()["error"]["details"][0]["reason"] == "CONTENT_TYPE_NOT_SUPPORTED"
    assert rpc.json()["error"]["code"] == -32005  # ContentTypeNotSupported
