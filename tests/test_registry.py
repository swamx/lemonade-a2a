"""The feature registry and compatibility manifest are data with rules; these tests enforce them."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import fields
from pathlib import Path

import pytest

from lemonade_a2a import registry
from lemonade_a2a.config import _ENV, Settings

ROOT = Path(__file__).resolve().parent.parent


def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_registry_is_structurally_sound() -> None:
    assert registry.validate() == []


def test_registry_and_manifest_match_their_json_schemas() -> None:
    jsonschema = pytest.importorskip("jsonschema")

    jsonschema.validate(registry.registry(), registry.schema("features.schema.json"))
    jsonschema.validate(registry.manifest(), registry.schema("compat.schema.json"))


def test_every_piece_of_evidence_exists() -> None:
    """``verified_by`` must name real tests, scripts and results: a claim without evidence is
    a bug in the registry, caught here and not by a user."""
    missing = []
    for item in registry.features():
        for reference in item["verified_by"]:
            if reference.startswith("tests/"):
                path, _, name = reference.partition("::")
                file = ROOT / path
                if not file.is_file() or name not in _test_functions(file):
                    missing.append(f"{item['id']}: {reference}")
            elif reference.startswith("script:"):
                if not (ROOT / reference.removeprefix("script:")).is_file():
                    missing.append(f"{item['id']}: {reference}")
            elif reference == "tck:official":
                results = json.loads(
                    (ROOT / "docs" / "conformance-results.json").read_text("utf-8")
                )
                if results["status"] != "passed":
                    missing.append(f"{item['id']}: TCK result is not passing")
            elif reference == "interop:clients":
                if not (ROOT / "interop").is_dir():
                    missing.append(f"{item['id']}: interop/ missing")
            else:
                missing.append(f"{item['id']}: unknown evidence kind {reference!r}")
    assert not missing, "\n".join(missing)


def test_supported_features_all_have_evidence() -> None:
    bare = [
        item["id"]
        for item in registry.features()
        if item["state"] == "supported" and not item["verified_by"]
    ]
    assert bare == []


def test_config_names_in_the_registry_are_real_settings() -> None:
    env_names = set(_ENV) | {"LEMONADE_A2A_CONFIG"}  # names the config file itself
    for item in registry.features():
        for name in re.findall(r"LEMONADE_[A-Z0-9_]+", item.get("config", "")):
            assert name in env_names, f"{item['id']} points at unknown setting {name}"


def test_toggles_point_at_real_settings_and_work() -> None:
    names = {f.name for f in fields(Settings)}
    for feature_id, toggle in registry.toggles().items():
        assert toggle["setting"] in names, feature_id
        on = Settings(features=f"+{feature_id}")
        off = Settings(features=f"-{feature_id}")
        assert getattr(on, toggle["setting"]) == toggle["on"]
        assert getattr(off, toggle["setting"]) == toggle["off"]


def test_toggling_a_feature_changes_what_resolve_reports() -> None:
    on = {r.id: r for r in registry.resolve(Settings(features="+adapter.cancel_on_disconnect"))}
    off = {r.id: r for r in registry.resolve(Settings())}

    assert on["adapter.cancel_on_disconnect"].active is True
    assert off["adapter.cancel_on_disconnect"].active is False
    assert off["a2a.binding.grpc"].active is False  # unsupported is never active
    assert off["lemonade.health_version"].active is None  # observed: not a behaviour
    assert off["a2a.streaming"].active is True


def test_every_setting_has_an_environment_variable_except_the_flag_only_ones() -> None:
    with_env = {field for field, _ in _ENV.values()}
    flag_only = {"streaming"}  # switched through LEMONADE_A2A_FEATURES=-a2a.streaming
    names = {f.name for f in fields(Settings)}

    assert names - with_env == flag_only


def test_unsupported_or_unknown_features_cannot_be_toggled() -> None:
    for bad in ("+a2a.binding.grpc", "-nonsense.id", "adapter.otel", "*a2a.streaming"):
        with pytest.raises(ValueError, match="FEATURES"):
            Settings(features=bad)


def test_the_manifest_describes_this_checkout() -> None:
    manifest = registry.manifest()

    assert manifest["a2a_protocol"]["supported"] == ["1.0"]
    assert manifest["a2a_sdk"]["declared"] == _declared_sdk_range()
    assert manifest["python"]["minimum"] == "3.11"


def _declared_sdk_range() -> str:
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    requirement = next(d for d in pyproject["project"]["dependencies"] if d.startswith("a2a-sdk"))
    return requirement.split("]", 1)[1]


def test_features_documentation_is_generated_from_the_registry() -> None:
    """docs/features.md must be regenerated (scripts/generate_features_doc.py) when the
    registry changes, so the two can never disagree."""
    from scripts.generate_features_doc import render

    current = (ROOT / "docs" / "features.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert current == render()
