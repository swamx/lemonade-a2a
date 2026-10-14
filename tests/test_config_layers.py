"""Layered configuration: defaults < config file < environment < command line."""

from __future__ import annotations

import json

import pytest

from lemonade_a2a.config import Settings, read_config_file


def _file(tmp_path, text: str):
    path = tmp_path / "lemonade-a2a.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_each_layer_overrides_the_one_below(tmp_path) -> None:
    config = _file(
        tmp_path, '[lemonade_a2a]\nport = 9201\nmodel = "from-file"\nagent_name = "file"\n'
    )
    env = {"LEMONADE_A2A_PORT": "9202", "LEMONADE_MODEL": "from-env"}

    settings, sources = Settings.load(config, env=env, overrides={"port": 9203})

    assert settings.port == 9203  # command line wins
    assert settings.model == "from-env"  # environment beats the file
    assert settings.agent_name == "file"  # only the file set it
    assert settings.host == "127.0.0.1"  # default
    assert sources["port"] == "cli" and sources["model"] == "env"
    assert sources["agent_name"] == "file" and "host" not in sources


def test_the_environment_alone_still_works_as_before() -> None:
    settings = Settings.from_env(
        {"LEMONADE_A2A_PORT": "9300", "LEMONADE_A2A_REASONING": "ARTIFACT"}
    )

    assert settings.port == 9300 and settings.reasoning == "artifact"


def test_the_config_path_can_come_from_the_environment(tmp_path) -> None:
    config = _file(tmp_path, "model = 'x'\n")  # the table name is optional

    settings, sources = Settings.load(env={"LEMONADE_A2A_CONFIG": str(config)})

    assert settings.model == "x" and sources["model"] == "file"


def test_a_file_cannot_smuggle_unknown_settings(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown setting"):
        read_config_file(_file(tmp_path, "[lemonade_a2a]\nprot = 1\n"))
    with pytest.raises(ValueError, match="unknown setting"):
        Settings.load(overrides={"nope": 1})


def test_bad_files_are_clear_errors(tmp_path) -> None:
    with pytest.raises(ValueError, match="not found"):
        read_config_file(tmp_path / "missing.toml")
    with pytest.raises(ValueError, match="not valid TOML"):
        read_config_file(_file(tmp_path, "this is = = not toml"))


def test_validation_applies_to_every_layer(tmp_path) -> None:
    config = _file(tmp_path, '[lemonade_a2a]\nhost = "0.0.0.0"\n')

    with pytest.raises(ValueError, match="loopback"):  # the profile rules still hold
        Settings.load(config, env={})
    settings, _ = Settings.load(config, env={}, overrides={"profile": "lan", "api_key": "k"})
    assert settings.host == "0.0.0.0"


def test_feature_flags_come_from_any_layer(tmp_path) -> None:
    config = _file(tmp_path, '[lemonade_a2a]\nfeatures = "+adapter.cancel_on_disconnect"\n')

    from_file, _ = Settings.load(config, env={})
    from_env, _ = Settings.load(env={"LEMONADE_A2A_FEATURES": "-a2a.streaming"})

    assert from_file.cancel_on_disconnect is True
    assert from_env.streaming is False


def test_secrets_are_redacted_in_the_effective_view() -> None:
    settings = Settings(api_key="topsecret", api_keys="a:hidden", lemonade_api_key="alsohidden")

    shown = json.dumps(settings.as_dict(redact=True))
    raw = settings.as_dict(redact=False)

    assert "topsecret" not in shown and "hidden" not in shown and "<set>" in shown
    assert raw["api_key"] == "topsecret"
    assert Settings().as_dict()["api_key"] == "<unset>"


def test_json_schema_describes_every_setting() -> None:
    schema = Settings.json_schema()

    assert schema["additionalProperties"] is False
    assert schema["properties"]["profile"]["enum"] == ["local", "lan", "external"]
    assert schema["properties"]["port"] == {
        "type": "integer",
        "default": 9100,
        "x-env": "LEMONADE_A2A_PORT",
    }
    assert schema["properties"]["api_key"]["writeOnly"] is True
    assert "topsecret" not in json.dumps(Settings(api_key="topsecret").json_schema())
    from dataclasses import fields

    assert set(schema["properties"]) == {f.name for f in fields(Settings)}


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"compat": "loud"}, "COMPAT"),
        ({"task_store": ""}, "TASK_STORE"),
        ({"backend": ""}, "BACKEND"),
    ],
)
def test_new_settings_are_validated(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**kwargs)


def test_plugin_authenticator_counts_as_authentication_for_exposed_profiles() -> None:
    with pytest.raises(ValueError, match="authentication"):
        Settings(profile="lan", host="0.0.0.0")
    Settings(profile="lan", host="0.0.0.0", authenticator="my-sso")
