"""Fixtures for tests that need a real Lemonade server.

Opt in with ``LEMONADE_INTEGRATION=1`` (optionally ``LEMONADE_BASE_URL`` and
``LEMONADE_MODEL``); otherwise everything here is skipped, so CI and ordinary
``pytest`` runs never download models or assume an accelerator.

When an integration test fails, ``integration-diagnostics/<test>.json`` is written
with versions, redacted settings, Lemonade's own health/system info and the tail of
the adapter log (see ``lemonade_a2a.diagnostics``).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from lemonade_a2a import diagnostics
from lemonade_a2a.config import Settings

DIAGNOSTICS_DIR = Path(os.getenv("LEMONADE_DIAGNOSTICS_DIR", "integration-diagnostics"))
_STATE: dict[str, object] = {}


def pytest_collection_modifyitems(config, items):
    if os.getenv("LEMONADE_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set LEMONADE_INTEGRATION=1 to run against a real Lemonade")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@dataclass(frozen=True)
class Adapter:
    url: str
    model: str
    lemonade_url: str
    log_path: Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def lemonade_url() -> str:
    return os.getenv("LEMONADE_BASE_URL", "http://127.0.0.1:13305/v1").rstrip("/")


@pytest.fixture(scope="session")
def model(lemonade_url: str) -> str:
    if os.getenv("LEMONADE_MODEL"):
        return os.environ["LEMONADE_MODEL"]
    try:
        data = httpx.get(f"{lemonade_url}/models", timeout=10).json().get("data") or []
    except (httpx.HTTPError, ValueError):
        pytest.skip(f"Lemonade not reachable at {lemonade_url}")
    if not data:
        pytest.skip("Lemonade reports no models; pull one first")
    return data[0]["id"]


@pytest.fixture(scope="session")
def adapter(lemonade_url: str, model: str, tmp_path_factory) -> Adapter:
    """The real adapter process (uvicorn) pointed at the real Lemonade server."""
    port = _free_port()
    log_path = tmp_path_factory.mktemp("adapter") / "adapter.log"
    env = {
        **os.environ,
        "LEMONADE_BASE_URL": lemonade_url,
        "LEMONADE_MODEL": model,
        "LEMONADE_A2A_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "LEMONADE_A2A_MAX_TASK_SECONDS": "300",
    }
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "lemonade_a2a.server:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(60):
                try:
                    if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.5)
            else:
                raise RuntimeError(f"adapter did not start; see {log_path}")
            _STATE["adapter"] = Adapter(url, model, lemonade_url, log_path)
            yield _STATE["adapter"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    adapter = _STATE.get("adapter")
    if report.when != "call" or not report.failed or "integration" not in item.keywords:
        return
    settings = Settings(
        lemonade_base_url=getattr(adapter, "lemonade_url", Settings().lemonade_base_url),
        model=getattr(adapter, "model", ""),
    )
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.name)
    path = DIAGNOSTICS_DIR / f"{safe_name}.json"
    snapshot = diagnostics.collect(settings, log_path=getattr(adapter, "log_path", None))
    snapshot["failed_test"] = item.nodeid
    snapshot["failure"] = str(report.longrepr)[-2000:]
    diagnostics.write(snapshot, path)
    report.sections.append(("diagnostics", f"wrote {path}"))
