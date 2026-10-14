"""The feature registry and the compatibility manifest (docs/specification.md).

Both are JSON files shipped inside the package (``lemonade_a2a/spec/``). The registry names
every capability with a stable id, its state and the evidence behind a ``supported`` claim;
the manifest records which component versions were tested. This module only reads them;
``doctor`` and ``capabilities`` build on it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

STATES = ("supported", "experimental", "unsupported", "deprecated", "observed", "unknown")
ID_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
DOMAINS = ("a2a", "adapter", "lemonade", "sdk")


def _load(name: str) -> dict[str, Any]:
    with resources.files("lemonade_a2a.spec").joinpath(name).open("rb") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def registry() -> dict[str, Any]:
    return _load("features.json")


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    return _load("compat.json")


def schema(name: str) -> dict[str, Any]:
    return _load(name)


def features() -> list[dict[str, Any]]:
    return registry()["features"]


def feature(feature_id: str) -> dict[str, Any] | None:
    return next((item for item in features() if item["id"] == feature_id), None)


@lru_cache(maxsize=1)
def toggles() -> dict[str, dict[str, Any]]:
    """``{feature id: toggle}`` for the features that ``LEMONADE_A2A_FEATURES`` may switch."""
    return {item["id"]: item["toggle"] for item in features() if "toggle" in item}


@dataclass(frozen=True)
class Resolved:
    """A registry entry as it applies to one configuration."""

    id: str
    title: str
    state: str
    active: bool | None  # None: not a switchable behaviour (observed / unknown)
    config: str
    notes: str
    verified_by: tuple[str, ...]


def is_active(item: dict[str, Any], settings: Any) -> bool | None:
    """Whether the feature is in effect for ``settings`` (``None`` when it is not a behaviour)."""
    if item["state"] in ("observed", "unknown"):
        return None
    if item["state"] in ("unsupported", "deprecated"):
        return False
    rule = item.get("active")
    if rule is None and "toggle" in item:
        rule = {"setting": item["toggle"]["setting"], "equals": item["toggle"]["on"]}
    if rule is None:
        return True
    value = getattr(settings, rule["setting"])
    return value == rule["equals"] if "equals" in rule else bool(value)


def resolve(settings: Any) -> list[Resolved]:
    return [
        Resolved(
            id=item["id"],
            title=item["title"],
            state=item["state"],
            active=is_active(item, settings),
            config=item.get("config", ""),
            notes=item.get("notes", ""),
            verified_by=tuple(item.get("verified_by", [])),
        )
        for item in features()
    ]


def validate(data: dict[str, Any] | None = None) -> list[str]:
    """Structural problems in a registry document (empty when it is sound)."""
    data = data or registry()
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(data.get("features", [])):
        feature_id = item.get("id", f"<entry {index}>")
        if feature_id in seen:
            problems.append(f"duplicate id {feature_id}")
        seen.add(feature_id)
        if not ID_PATTERN.match(str(feature_id)) or feature_id.split(".")[0] not in DOMAINS:
            problems.append(f"{feature_id}: id must be <domain>.<name> with a domain of {DOMAINS}")
        if item.get("state") not in STATES:
            problems.append(f"{feature_id}: state must be one of {STATES}")
        for required in ("title", "since", "verified_by"):
            if required not in item:
                problems.append(f"{feature_id}: missing {required}")
        if item.get("state") == "supported" and not item.get("verified_by"):
            problems.append(f"{feature_id}: a supported feature needs verified_by evidence")
        toggle = item.get("toggle")
        if toggle and not {"setting", "on", "off"} <= set(toggle):
            problems.append(f"{feature_id}: toggle needs setting, on and off")
    return problems
