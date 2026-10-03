from lemonade_a2a.agent_card import build_agent_card
from lemonade_a2a.config import Settings


def test_agent_card_uses_public_url_and_local_skill() -> None:
    settings = Settings(public_url="http://localhost:9000")
    card = build_agent_card(settings)

    assert card["url"] == "http://localhost:9000"
    assert card["capabilities"]["streaming"] is False
    assert card["skills"][0]["id"] == "local-chat"
    assert "Lemonade" in card["skills"][0]["description"]


def test_agent_card_does_not_claim_unconfigured_modalities() -> None:
    card = build_agent_card(Settings())
    skill_ids = {skill["id"] for skill in card["skills"]}

    assert "vision" not in skill_ids
    assert "speech" not in skill_ids


def test_default_lemonade_url_uses_server_documented_endpoint() -> None:
    assert Settings().lemonade_base_url == "http://localhost:13305/v1"


def test_settings_from_env_uses_dataclass_defaults(monkeypatch) -> None:
    for name in (
        "LEMONADE_BASE_URL",
        "LEMONADE_MODEL",
        "LEMONADE_A2A_HOST",
        "LEMONADE_A2A_PORT",
        "LEMONADE_A2A_PUBLIC_URL",
        "LEMONADE_A2A_AGENT_NAME",
        "LEMONADE_A2A_AGENT_DESCRIPTION",
    ):
        monkeypatch.delenv(name, raising=False)

    assert Settings.from_env() == Settings()
