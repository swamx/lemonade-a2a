import pytest

from lemonade_a2a import __version__
from lemonade_a2a.config import DEFAULT_PORT, Settings
from lemonade_a2a.server import build_agent_card


def test_agent_card_uses_public_url_and_local_skill() -> None:
    card = build_agent_card(Settings(public_url="http://localhost:9100"))

    assert all(i.url == "http://localhost:9100" for i in card.supported_interfaces)
    assert card.capabilities.streaming is True
    assert card.skills[0].id == "local-chat"
    assert "Lemonade" in card.skills[0].description
    assert card.version == __version__


def test_agent_card_is_hardware_agnostic() -> None:
    card = build_agent_card(Settings())

    assert "AMD" not in card.description
    assert "amd" not in card.skills[0].tags


def test_agent_card_does_not_claim_unconfigured_modalities() -> None:
    skill_ids = {skill.id for skill in build_agent_card(Settings()).skills}

    assert "vision" not in skill_ids
    assert "speech" not in skill_ids


def test_default_lemonade_url_uses_server_documented_endpoint() -> None:
    assert Settings().lemonade_base_url == "http://localhost:13305/v1"


def test_default_port_does_not_clash_with_lemonade_websocket() -> None:
    assert DEFAULT_PORT != 9000


ENV_NAMES = (
    "LEMONADE_BASE_URL",
    "LEMONADE_MODEL",
    "LEMONADE_A2A_HOST",
    "LEMONADE_A2A_PORT",
    "LEMONADE_A2A_PUBLIC_URL",
    "LEMONADE_A2A_AGENT_NAME",
    "LEMONADE_A2A_AGENT_DESCRIPTION",
    "LEMONADE_TIMEOUT_SECONDS",
    "LEMONADE_A2A_MAX_INPUT_CHARS",
)


def test_settings_from_env_uses_dataclass_defaults(monkeypatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    assert Settings.from_env() == Settings()


def test_settings_from_env_reads_limits(monkeypatch) -> None:
    monkeypatch.setenv("LEMONADE_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("LEMONADE_A2A_MAX_INPUT_CHARS", "42")

    settings = Settings.from_env()

    assert settings.request_timeout_seconds == 7.5
    assert settings.max_input_chars == 42


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": 0},
        {"port": 70000},
        {"request_timeout_seconds": 0},
        {"max_input_chars": 0},
        {"public_url": ""},
    ],
)
def test_settings_reject_invalid_values(kwargs) -> None:
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_example_agent_card_matches_generated_card() -> None:
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from lemonade_a2a.server import create_app

    example = json.loads(Path("examples/agent-card.json").read_text(encoding="utf-8"))
    generated = TestClient(create_app()).get("/.well-known/agent-card.json").json()

    assert example == generated


def test_settings_read_resource_and_security_env(monkeypatch) -> None:
    values = {
        "LEMONADE_A2A_MAX_INPUT_PARTS": "4",
        "LEMONADE_A2A_MAX_TASK_SECONDS": "30",
        "LEMONADE_A2A_MAX_CONCURRENT_TASKS": "2",
        "LEMONADE_A2A_MAX_STORED_TASKS": "50",
        "LEMONADE_A2A_API_KEY": "k1",
        "LEMONADE_API_KEY": "k2",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()

    assert (settings.max_input_parts, settings.max_task_seconds) == (4, 30.0)
    assert (settings.max_concurrent_tasks, settings.max_stored_tasks) == (2, 50)
    assert (settings.api_key, settings.lemonade_api_key) == ("k1", "k2")


def test_tls_files_must_be_given_together() -> None:
    with pytest.raises(ValueError):
        Settings(ssl_certfile="cert.pem")
    Settings(ssl_certfile="cert.pem", ssl_keyfile="key.pem")
