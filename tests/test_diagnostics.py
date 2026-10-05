import json

import httpx
import pytest

from lemonade_a2a import diagnostics
from lemonade_a2a.config import Settings


def _transport(routes: dict[str, httpx.Response]):
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(request.url.path, httpx.Response(404, json={"detail": "no"}))

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch):
    original = httpx.Client

    def install(routes):
        monkeypatch.setattr(
            httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": _transport(routes)})
        )

    return install


def test_collect_reports_lemonade_state_and_redacts_secrets(patched_client) -> None:
    patched_client(
        {
            "/api/v1/health": httpx.Response(200, json={"status": "ok"}),
            "/api/v1/system-info": httpx.Response(200, json={"Processor": "test-cpu"}),
            "/v1/models": httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]}),
        }
    )
    settings = Settings(api_key="super-secret", lemonade_api_key="also-secret", model="m1")

    report = diagnostics.collect(settings)

    assert report["lemonade"]["models"] == ["m1", "m2"]
    assert report["lemonade"]["health"] == {"status": "ok"}
    assert report["lemonade"]["system_info"]["Processor"] == "test-cpu"
    assert report["settings"]["api_key"] == "***set***"
    assert report["settings"]["lemonade_api_key"] == "***set***"
    assert report["settings"]["model"] == "m1"
    assert "super-secret" not in json.dumps(report)
    assert report["packages"]["lemonade-a2a"] != "not installed"


def test_failing_probes_are_recorded_not_raised(patched_client) -> None:
    patched_client({})  # everything answers 404

    report = diagnostics.collect(Settings())

    assert "error" in report["lemonade"]["health"]
    assert "error" in report["lemonade"]["models"]


def test_unreachable_server_is_recorded(monkeypatch) -> None:
    original = httpx.Client

    def refuse(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **k: original(*a, **{**k, "transport": httpx.MockTransport(refuse)}),
    )

    report = diagnostics.collect(Settings())

    assert "ConnectError" in report["lemonade"]["health"]["error"]


def test_log_tail_is_included_and_bounded(tmp_path, patched_client) -> None:
    patched_client({})
    log = tmp_path / "adapter.log"
    log.write_text("\n".join(f"line {i}" for i in range(500)), encoding="utf-8")

    report = diagnostics.collect(Settings(), log_path=log)

    assert len(report["adapter_log_tail"]) == diagnostics.LOG_TAIL_LINES
    assert report["adapter_log_tail"][-1] == "line 499"


def test_missing_log_file_is_reported(tmp_path, patched_client) -> None:
    patched_client({})

    report = diagnostics.collect(Settings(), log_path=tmp_path / "missing.log")

    assert "could not read" in report["adapter_log_tail"][0]


def test_write_creates_parent_directories(tmp_path) -> None:
    target = diagnostics.write({"a": 1}, tmp_path / "nested" / "diag.json")

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_cli_prints_a_report(monkeypatch, capsys, patched_client) -> None:
    patched_client({})
    monkeypatch.setattr("sys.argv", ["diagnostics", "--lemonade-url", "http://x:1/v1"])

    diagnostics.main()

    printed = json.loads(capsys.readouterr().out)
    assert printed["lemonade"]["base_url"] == "http://x:1/v1"
