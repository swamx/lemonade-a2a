"""Shared fixtures."""

import copy
import sys

import pytest

from lemonade_a2a import __version__, compat, registry
from tests.plugin_helpers import installed  # noqa: F401  (a fixture, used by name)


@pytest.fixture
def covered_manifest(monkeypatch):
    """The shipped manifest, widened so that *this* environment counts as tested.

    The shipped manifest lists the versions the project tested. A canary run (or a user) may have
    a different, perfectly valid ``a2a-sdk`` or Python installed; tests that are about everything
    *other* than "is this exact version in the list" use this so they do not depend on it.
    """
    manifest = copy.deepcopy(registry.manifest())
    sdk = compat.installed_version("a2a-sdk")
    manifest["a2a_sdk"]["tested"] = sorted({*manifest["a2a_sdk"]["tested"], sdk})
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    manifest["python"]["tested"] = sorted({*manifest["python"]["tested"], python})
    manifest["adapter"]["version"] = __version__
    monkeypatch.setattr(registry, "manifest", lambda: manifest)
    return manifest
