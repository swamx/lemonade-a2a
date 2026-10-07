"""Fake third-party plugins for tests: register entry points without installing a package."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lemonade_a2a import plugins


class FakeEntryPoint:
    """Stands in for an installed package's entry point."""

    def __init__(
        self, name: str, factory, *, fail: bool = False, package: str = "acme-plugin"
    ) -> None:
        self.name = name
        self.value = f"acme_plugin:{name}"
        self._factory = factory
        self._fail = fail
        self.dist = SimpleNamespace(metadata={"Name": package}, version="2.0.0")

    def load(self):
        if self._fail:
            raise ImportError("acme_plugin is broken")
        return self._factory


@pytest.fixture
def installed(monkeypatch):
    """Register fake third-party plugins: ``installed("group", FakeEntryPoint(...))``."""
    registry: dict[str, list[FakeEntryPoint]] = {}
    monkeypatch.setattr(plugins, "_entry_points", lambda group: registry.get(group, []))

    def add(group: str, entry: FakeEntryPoint) -> None:
        registry.setdefault(plugins.GROUPS[group], []).append(entry)

    return add
