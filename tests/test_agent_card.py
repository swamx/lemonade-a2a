from lemonade_a2a.agent_card import build_agent_card
from lemonade_a2a.config import Settings


def test_agent_card_uses_public_url_and_local_skill() -> None:
    settings = Settings(public_url="http://localhost:9000")
    card = build_agent_card(settings)

    assert card["url"] == "http://localhost:9000"
    assert card["capabilities"]["streaming"] is True
    assert card["skills"][0]["id"] == "local-chat"
    assert "Lemonade" in card["skills"][0]["description"]


def test_agent_card_does_not_claim_unconfigured_modalities() -> None:
    card = build_agent_card(Settings())
    skill_ids = {skill["id"] for skill in card["skills"]}

    assert "vision" not in skill_ids
    assert "speech" not in skill_ids
