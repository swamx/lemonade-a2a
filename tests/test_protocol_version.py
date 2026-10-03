from lemonade_a2a.config import Settings
from lemonade_a2a.server import build_agent_card


def test_agent_card_advertises_a2a_v1() -> None:
    card = build_agent_card(Settings())
    versions = {interface.protocol_version for interface in card.supported_interfaces}
    assert "1.0" in versions


def test_agent_card_has_jsonrpc_and_http_json_bindings() -> None:
    card = build_agent_card(Settings())
    bindings = {interface.protocol_binding for interface in card.supported_interfaces}
    assert "JSONRPC" in bindings
    assert "HTTP+JSON" in bindings
