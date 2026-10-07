"""The shipped dashboards and alerts must only name metrics the adapter really exposes."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from lemonade_a2a.config import Settings
from lemonade_a2a.executor import LemonadeAgentExecutor
from lemonade_a2a.lemonade_client import LemonadeClient
from lemonade_a2a.server import create_app
from lemonade_a2a.telemetry import setup_telemetry

yaml = pytest.importorskip("yaml")

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "observability"
HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
METRIC = re.compile(r"\b(lemonade_a2a_[a-z_]+|http_server_request_duration_seconds)\b")
# Needs a live streaming client going away; covered by tests/test_live_lifecycle.py.
NOT_EXERCISED_HERE = {"lemonade_a2a_disconnects_total"}


class Works:
    async def stream(self, messages):
        yield "o"
        yield "k"


class Broken:
    async def stream(self, messages):
        raise httpx.ConnectError("down")
        yield  # pragma: no cover


def _exposed_metric_names(monkeypatch) -> set[str]:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    settings = Settings(
        otel_enabled=True,
        otel_prometheus=True,
        api_key="k",
        max_stored_tasks=1,
        max_request_bytes=4000,
    )
    telemetry = setup_telemetry(settings)
    # Exercise the paths that create counters: success, failure, auth refusal, oversize, eviction.
    names: set[str] = set()
    flaky = LemonadeClient("http://backend/v1", "m", telemetry=telemetry)

    def refuse(request):
        raise httpx.ReadTimeout("slow")

    flaky._http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    for client_cls in (Works, Broken, flaky):
        backend = client_cls() if isinstance(client_cls, type) else client_cls
        executor = LemonadeAgentExecutor(backend, telemetry=telemetry)
        client = TestClient(create_app(settings, executor=executor, telemetry=telemetry))
        auth = {**HEADERS, "Authorization": "Bearer k"}
        for index in range(3):
            body = {
                "message": {
                    "messageId": f"m{index}{id(client_cls)}",
                    "role": "ROLE_USER",
                    "parts": [{"text": "x"}],
                }
            }
            client.post("/message:send", headers=auth, content=json.dumps(body))
        client.get("/tasks", headers=HEADERS)  # 401
        client.post("/message:send", headers=auth, content=b"x" * 9000)  # 413
        time.sleep(0.3)
        names |= {
            line.split("{")[0].split(" ")[0]
            for line in client.get("/metrics", headers=auth).text.splitlines()
            if line and not line.startswith("#")
        }
    telemetry.shutdown()
    return names


def _base(name: str) -> str:
    return re.sub(r"_(bucket|count|sum)$", "", name)


def test_the_dashboard_only_queries_metrics_that_exist(monkeypatch) -> None:
    dashboard = json.loads(
        (EXAMPLES / "grafana" / "dashboards" / "lemonade-a2a.json").read_text("utf-8")
    )
    queried = {
        _base(name)
        for panel in dashboard["panels"]
        for target in panel["targets"]
        for name in METRIC.findall(target["expr"])
    }
    exposed = {_base(name) for name in _exposed_metric_names(monkeypatch)}

    missing = queried - exposed - NOT_EXERCISED_HERE
    assert not missing, f"dashboard queries metrics the adapter does not expose: {sorted(missing)}"


def test_the_alerts_only_use_metrics_that_exist(monkeypatch) -> None:
    rules = yaml.safe_load((EXAMPLES / "alerts.yml").read_text("utf-8"))
    used = {
        _base(name)
        for group in rules["groups"]
        for rule in group["rules"]
        for name in METRIC.findall(rule["expr"])
    }
    exposed = {_base(name) for name in _exposed_metric_names(monkeypatch)}

    assert not (used - exposed - NOT_EXERCISED_HERE), sorted(used - exposed)


@pytest.mark.parametrize(
    "name",
    [
        "docker-compose.yml",
        "otel-collector.yaml",
        "prometheus.yml",
        "alerts.yml",
        "grafana/provisioning/datasources/prometheus.yml",
        "grafana/provisioning/dashboards/dashboards.yml",
    ],
)
def test_example_configuration_is_valid_yaml(name: str) -> None:
    assert isinstance(yaml.safe_load((EXAMPLES / name).read_text("utf-8")), dict)


def test_the_collector_scrubs_content_even_if_capture_is_misconfigured() -> None:
    config = yaml.safe_load((EXAMPLES / "otel-collector.yaml").read_text("utf-8"))

    assert "attributes/no-content" in config["service"]["pipelines"]["traces"]["processors"]
    assert config["processors"]["attributes/no-content"]["actions"][0] == {
        "key": "content",
        "action": "delete",
    }
