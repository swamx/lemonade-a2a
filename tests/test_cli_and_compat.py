"""``lemonade-a2a`` commands, compatibility checks, the startup gate and the capabilities endpoint."""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lemonade_a2a import cli, compat, registry, sdk_gap
from lemonade_a2a.compat import FAIL, PASS, UNKNOWN, WARN, Check
from lemonade_a2a.config import Settings
from lemonade_a2a.executor import LemonadeAgentExecutor
from lemonade_a2a.server import (
    CAPABILITIES_EXTENSION,
    CAPABILITIES_PATH,
    create_app,
    startup_gate,
)
from tests.live import free_port, live_adapter, serve
from tests.mock_lemonade import app as mock_app
from tests.plugin_helpers import FakeEntryPoint
from tests.telemetry_helpers import Recorder

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}

MANIFEST = {
    "python": {"tested": ["3.11", "3.12"], "minimum": "3.11"},
    "adapter": {"version": "0.1.0"},
    "a2a_protocol": {"supported": ["1.0"]},
    "a2a_sdk": {
        "declared": ">=1.2.0,<1.3",
        "tested": ["1.2.0", "1.2.1"],
        "known_broken": [{"range": "==1.2.9", "reason": "dispatcher bug"}],
    },
    "lemonade": {"tested": ["2026.40.0"], "minimum": "2026.30.0"},
}


def _verdict(check: Check) -> str:
    return check.verdict


# --- verdict arithmetic -------------------------------------------------------------------


def _c(*verdicts: str) -> list[Check]:
    return [Check("x", "x", v) for v in verdicts]


@pytest.mark.parametrize(
    ("verdicts", "strict", "code"),
    [
        ((PASS, PASS), False, 0),
        ((PASS, WARN), False, 1),
        ((PASS, WARN), True, 2),
        ((PASS, FAIL), False, 2),
        ((PASS, UNKNOWN), False, 3),
        ((WARN, UNKNOWN), False, 3),  # could not check outranks a warning
        ((FAIL, UNKNOWN), False, 2),  # a failure outranks everything
        ((), False, 0),
    ],
)
def test_exit_codes(verdicts, strict, code) -> None:
    assert compat.exit_code(_c(*verdicts), strict=strict) == code


# --- component checks ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "verdict"),
    [
        ("1.2.1", PASS),  # tested
        ("1.2.2", WARN),  # inside the declared range, not tested
        ("1.2.9", FAIL),  # known broken
        ("1.3.0", FAIL),  # outside the declared range
        ("1.1.5", FAIL),
        (None, FAIL),  # not installed
    ],
)
def test_sdk_check(monkeypatch, version, verdict) -> None:
    monkeypatch.setattr(compat, "installed_version", lambda name: version)

    check = compat.check_sdk(MANIFEST)

    assert check.verdict == verdict
    if verdict != PASS:
        assert check.hint or version is None


@pytest.mark.parametrize(
    ("version", "verdict"),
    [
        ("2026.40.0", PASS),
        ("2026.41.0", WARN),  # newer than tested
        ("2026.35.0", WARN),  # between minimum and tested, never tested
        ("2026.20.0", FAIL),  # below the minimum
        (None, UNKNOWN),
        ("garbage", UNKNOWN),
    ],
)
def test_lemonade_version_check(version, verdict) -> None:
    assert compat.check_lemonade_version(MANIFEST, version).verdict == verdict


def test_python_check() -> None:
    assert compat.check_python({"python": {"tested": ["3.11"], "minimum": "3.11"}}).verdict in (
        PASS,
        WARN,
    )
    assert compat.check_python({"python": {"tested": ["9.9"], "minimum": "3.11"}}).verdict == WARN
    assert compat.check_python({"python": {"tested": ["9.9"], "minimum": "9.8"}}).verdict == FAIL


def test_adapter_version_mismatch_is_a_warning() -> None:
    assert compat.check_adapter({"adapter": {"version": "9.9.9"}}).verdict == WARN
    assert compat.check_adapter({"adapter": {}}).verdict == PASS


def test_the_shipped_manifest_is_consistent() -> None:
    """What the project claims must hang together: tested versions are inside the declared range
    and not known broken, and the manifest was generated for this adapter version."""
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    manifest = registry.manifest()
    declared = SpecifierSet(manifest["a2a_sdk"]["declared"])

    assert manifest["a2a_sdk"]["tested"], "no tested a2a-sdk version"
    for version in manifest["a2a_sdk"]["tested"]:
        assert Version(version) in declared, f"{version} is tested but outside {declared}"
        for broken in manifest["a2a_sdk"].get("known_broken", []):
            assert Version(version) not in SpecifierSet(broken["range"]), (
                f"{version} is also known broken"
            )
    assert manifest["adapter"]["version"] == __import__("lemonade_a2a").__version__
    assert (
        manifest["lemonade"]["minimum"] in manifest["lemonade"]["tested"]
        or manifest["lemonade"]["tested"]
    )


def test_local_checks_pass_for_a_covered_install(covered_manifest) -> None:
    checks = compat.local_checks(Settings())

    assert [c for c in checks if c.verdict != PASS] == []


def test_an_uncovered_sdk_is_a_warning_not_a_failure(monkeypatch) -> None:
    """The case the canary exposed: a valid SDK the manifest has not tested yet."""
    monkeypatch.setattr(
        compat, "installed_version", lambda name: "1.2.99" if name == "a2a-sdk" else None
    )

    check = compat.check_sdk(registry.manifest())

    assert check.verdict == WARN and "not tested" in check.detail


def test_telemetry_check_fails_when_the_extra_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(compat, "installed_version", lambda name: None)

    assert compat.check_telemetry(Settings(otel_enabled=True)).verdict == FAIL


# --- probing a (mock) Lemonade --------------------------------------------------------------


@pytest.fixture
def lemonade(monkeypatch):
    port = free_port()
    with serve(mock_app, port):
        yield Settings(lemonade_base_url=f"http://127.0.0.1:{port}/v1", model="mock-model")


def _by_id(checks: list[Check]) -> dict[str, Check]:
    return {c.id: c for c in checks}


def test_a_healthy_lemonade_passes(lemonade) -> None:
    checks = _by_id(compat.probe_lemonade(lemonade, MANIFEST))

    assert checks["lemonade"].verdict == PASS
    assert checks["lemonade_version"].verdict == PASS
    assert checks["lemonade_model"].verdict == PASS and "32768" in checks["lemonade_model"].detail


def test_a_newer_lemonade_warns(lemonade, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_LEMONADE_VERSION", "2027.1.0")

    assert _by_id(compat.probe_lemonade(lemonade, MANIFEST))["lemonade_version"].verdict == WARN


def test_a_missing_model_fails(lemonade) -> None:
    settings = Settings(lemonade_base_url=lemonade.lemonade_base_url, model="ghost")

    assert _by_id(compat.probe_lemonade(settings, MANIFEST))["lemonade_model"].verdict == FAIL


def test_a_tiny_context_window_is_flagged(lemonade, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_LEMONADE_CONTEXT", "1459")  # what 12B gets on an 8 GB GPU

    check = _by_id(compat.probe_lemonade(lemonade, MANIFEST))["lemonade_model"]

    assert check.verdict == WARN and "1459" in check.detail and "context" in check.hint


def test_an_unreachable_lemonade_is_unknown_not_failed() -> None:
    settings = Settings(lemonade_base_url=f"http://127.0.0.1:{free_port()}/v1")

    checks = compat.probe_lemonade(settings, MANIFEST)

    assert [c.verdict for c in checks] == [UNKNOWN]
    assert compat.exit_code(checks) == 3


def test_the_deep_probe_runs_a_tiny_generation(lemonade) -> None:
    assert (
        _by_id(compat.probe_lemonade(lemonade, MANIFEST, deep=True))["lemonade_stream"].verdict
        == PASS
    )


def test_the_deep_probe_sees_errors_inside_the_stream(lemonade, monkeypatch) -> None:
    monkeypatch.setenv("MOCK_LEMONADE_ERROR", "exceed_context_size_error")

    check = _by_id(compat.probe_lemonade(lemonade, MANIFEST, deep=True))["lemonade_stream"]

    assert check.verdict == FAIL and "exceed_context_size_error" in check.detail


def test_the_deep_probe_needs_a_model() -> None:
    port = free_port()
    with serve(mock_app, port):
        settings = Settings(lemonade_base_url=f"http://127.0.0.1:{port}/v1")
        assert (
            _by_id(compat.probe_lemonade(settings, MANIFEST, deep=True))["lemonade_stream"].verdict
            == UNKNOWN
        )


# --- probing a running adapter ------------------------------------------------------------------


def test_a_running_adapter_matches_the_registry() -> None:
    with live_adapter() as base:
        checks = _by_id(compat.probe_adapter(base, Settings(), MANIFEST))

    assert checks["adapter_card"].verdict == PASS
    assert checks["protocol"].verdict == PASS
    assert checks["card_registry"].verdict == PASS


def test_a_card_declaring_more_than_the_registry_supports_fails(monkeypatch) -> None:
    card = {
        "name": "x",
        "version": "1",
        "supportedInterfaces": [{"protocolVersion": "1.0"}],
        "capabilities": {"pushNotifications": True, "streaming": True},
    }

    class Reply:
        def json(self):
            return card

    monkeypatch.setattr(compat.httpx, "get", lambda *a, **k: Reply())

    assert (
        _by_id(compat.probe_adapter("http://x", Settings(), MANIFEST))["card_registry"].verdict
        == FAIL
    )


def test_untested_protocol_versions_fail(monkeypatch) -> None:
    card = {
        "name": "x",
        "version": "1",
        "supportedInterfaces": [{"protocolVersion": "2.0"}],
        "capabilities": {},
    }

    class Reply:
        def json(self):
            return card

    monkeypatch.setattr(compat.httpx, "get", lambda *a, **k: Reply())

    assert (
        _by_id(compat.probe_adapter("http://x", Settings(), MANIFEST))["protocol"].verdict == FAIL
    )


def test_no_adapter_running_is_unknown() -> None:
    checks = compat.probe_adapter(f"http://127.0.0.1:{free_port()}", Settings(), MANIFEST)

    assert [c.verdict for c in checks] == [UNKNOWN]


# --- the command line ---------------------------------------------------------------------------


def _run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_version(capsys) -> None:
    code, out, _ = _run(capsys, "version")

    assert code == 0 and "lemonade-a2a 0.1.0" in out and "a2a-sdk" in out and "specification" in out


def test_capabilities_text_and_json(capsys) -> None:
    code, out, _ = _run(capsys, "capabilities")
    assert code == 0 and "a2a.streaming" in out and "a2a.binding.grpc" not in out
    _, out_all, _ = _run(capsys, "capabilities", "--all")
    assert "a2a.binding.grpc" in out_all and "lemonade.health_version" in out_all

    _, out_json, _ = _run(capsys, "capabilities", "--json")
    data = json.loads(out_json)
    streaming = next(f for f in data["features"] if f["id"] == "a2a.streaming")
    assert streaming["active"] is True and streaming["verified_by"]


def test_capabilities_follow_the_configuration(capsys, monkeypatch) -> None:
    monkeypatch.setenv("LEMONADE_A2A_FEATURES", "+adapter.cancel_on_disconnect,-a2a.streaming")

    _, out, _ = _run(capsys, "capabilities", "--json")

    state = {f["id"]: f["active"] for f in json.loads(out)["features"]}
    assert state["adapter.cancel_on_disconnect"] is True and state["a2a.streaming"] is False


def test_config_show_reports_sources(capsys, monkeypatch, tmp_path) -> None:
    config = tmp_path / "c.toml"
    config.write_text('[lemonade_a2a]\nagent_name = "Kitchen"\n', encoding="utf-8")
    monkeypatch.setenv("LEMONADE_A2A_PORT", "9400")
    monkeypatch.setenv("LEMONADE_A2A_API_KEY", "top-secret-value")

    code, out, _ = _run(capsys, "--config", str(config), "config", "show", "--json")

    shown = json.loads(out)
    assert code == 0
    assert shown["agent_name"] == {"value": "Kitchen", "source": "file"}
    assert shown["port"] == {"value": 9400, "source": "env"}
    assert shown["api_key"]["value"] == "<set>" and "top-secret-value" not in out
    assert shown["host"]["source"] == "default"


def test_config_validate_and_errors(capsys, monkeypatch) -> None:
    assert _run(capsys, "config", "validate")[0] == 0

    monkeypatch.setenv("LEMONADE_A2A_HOST", "0.0.0.0")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["config", "validate"])
    assert exit_info.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_config_schema_is_valid_json(capsys) -> None:
    code, out, _ = _run(capsys, "config", "schema")

    assert code == 0 and json.loads(out)["title"] == "lemonade-a2a settings"


def test_doctor_without_lemonade(capsys, covered_manifest) -> None:
    code, out, _ = _run(capsys, "doctor", "--no-lemonade", "--json")

    report = json.loads(out)
    assert code == 0 and report["verdict"] == "pass" and report["exit_code"] == 0
    assert {"python", "a2a_sdk", "registry", "config"} <= {c["id"] for c in report["checks"]}


def test_doctor_text_output_ends_with_a_verdict(capsys, covered_manifest) -> None:
    code, out, _ = _run(capsys, "doctor", "--no-lemonade", "--sdk-gap")

    assert code == 0
    assert "Installed components" in out and "Verdict: PASS" in out
    assert "beyond what the adapter uses" in out


def test_doctor_json_with_backend_and_adapter(
    capsys, lemonade, monkeypatch, covered_manifest
) -> None:
    monkeypatch.setenv("LEMONADE_BASE_URL", lemonade.lemonade_base_url)
    monkeypatch.setenv("LEMONADE_MODEL", "mock-model")
    with live_adapter() as base:
        code, out, _ = _run(capsys, "doctor", "--json", "--deep", "--adapter-url", base)

    report = json.loads(out[out.index("{") :])  # the mock prints a line of its own first
    ids = {c["id"] for c in report["checks"]}
    assert code == 0, report
    assert {"lemonade", "lemonade_version", "lemonade_model", "lemonade_stream", "protocol"} <= ids


def test_doctor_strict_turns_warnings_into_failures(capsys, lemonade, monkeypatch) -> None:
    monkeypatch.setenv("LEMONADE_BASE_URL", lemonade.lemonade_base_url)
    monkeypatch.setenv("MOCK_LEMONADE_VERSION", "2099.1.0")  # newer than tested: a warning

    relaxed, _, _ = _run(capsys, "doctor", "--json")
    strict, out, _ = _run(capsys, "doctor", "--json", "--strict")

    assert relaxed == 1 and strict == 2
    assert json.loads(out)["verdict"] == "fail"


def test_doctor_exit_code_when_lemonade_is_down(capsys, monkeypatch) -> None:
    monkeypatch.setenv("LEMONADE_BASE_URL", f"http://127.0.0.1:{free_port()}/v1")

    code, out, _ = _run(capsys, "doctor", "--json")

    assert code == 3 and json.loads(out)["verdict"] == "unknown"


def test_the_bare_command_and_flags_mean_serve(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr("lemonade_a2a.server.serve", lambda settings: seen.append(settings))

    assert cli.main([]) == 0
    assert cli.main(["--port", "9411"]) == 0
    assert cli.main(["serve", "--port", "9412", "--log-format", "json"]) == 0

    assert [s.port for s in seen] == [9100, 9411, 9412]
    assert seen[2].log_format == "json"


def test_config_file_and_flags_reach_serve(monkeypatch, tmp_path) -> None:
    config = tmp_path / "c.toml"
    config.write_text('[lemonade_a2a]\nport = 9500\nmodel = "m"\n', encoding="utf-8")
    seen = []
    monkeypatch.setattr("lemonade_a2a.server.serve", lambda settings: seen.append(settings))

    cli.main(["--config", str(config)])
    cli.main(["--config", str(config), "serve", "--port", "9501"])

    assert [(s.port, s.model) for s in seen] == [(9500, "m"), (9501, "m")]


def test_support_bundle_is_redacted(capsys, tmp_path, monkeypatch, lemonade) -> None:
    monkeypatch.setenv("LEMONADE_BASE_URL", lemonade.lemonade_base_url)
    monkeypatch.setenv("LEMONADE_A2A_API_KEY", "SECRET-ADAPTER-KEY")
    monkeypatch.setenv("LEMONADE_API_KEY", "SECRET-BACKEND-KEY")
    monkeypatch.setenv("LEMONADE_A2A_API_KEYS", "alice:SECRET-ALICE-KEY")
    log = tmp_path / "adapter.log"
    log.write_text(f"started in {Path.home()}\nnormal line\n", encoding="utf-8")
    output = tmp_path / "bundle.zip"

    code, out, _ = _run(capsys, "support-bundle", "--output", str(output), "--log", str(log))

    assert code == 0 and "review it before sharing" in out
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        text = "\n".join(archive.read(n).decode("utf-8") for n in names)
    assert {
        "diagnostics.json",
        "doctor.json",
        "settings.json",
        "features.json",
        "manifest.json",
        "README.txt",
    } <= names
    assert "SECRET-" not in text
    assert str(Path.home()) not in text and "<home>" in text
    assert json.loads(zipfile.ZipFile(output).read("doctor.json"))["checks"]


# --- SDK gap -----------------------------------------------------------------------------------------


def test_sdk_gap_reports_what_this_sdk_offers() -> None:
    gaps = {g.id: g for g in sdk_gap.probe()}

    assert gaps["push"].available and gaps["push"].adapter == "not offered"
    assert gaps["db_store"].available and gaps["db_store"].adapter == "supported"
    assert gaps["queue_manager"].available and gaps["queue_manager"].adapter == "not used"
    assert "push" in {g.id for g in sdk_gap.opportunities()}
    assert "db_store" not in {g.id for g in sdk_gap.opportunities()}


def test_sdk_gap_handles_missing_modules() -> None:
    assert sdk_gap._available("a2a.does_not_exist", None) is False
    assert (
        sdk_gap._available("a2a.server.request_handlers.default_request_handler_v2", "Nope:param")
        is False
    )


# --- the startup gate ------------------------------------------------------------------------------------


@pytest.fixture
def broken_manifest(monkeypatch):
    bad = {**MANIFEST, "a2a_sdk": {"declared": ">=9.0", "tested": ["9.0.0"]}}
    monkeypatch.setattr(registry, "manifest", lambda: bad)


def test_strict_mode_refuses_to_start_on_a_failed_check(broken_manifest) -> None:
    with pytest.raises(SystemExit, match="refusing to start"):
        startup_gate(Settings(compat="strict"))


def test_warn_mode_logs_and_starts(broken_manifest, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="lemonade_a2a"):
        startup_gate(Settings(compat="warn"))

    assert "compat fail" in caplog.text and "a2a-sdk" in caplog.text


def test_off_mode_does_not_look(broken_manifest, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="lemonade_a2a"):
        startup_gate(Settings(compat="off"))

    assert caplog.text == ""


def test_a_clean_install_starts_in_strict_mode(covered_manifest) -> None:
    startup_gate(Settings(compat="strict"))


def test_the_compat_result_is_exported_as_a_metric(broken_manifest) -> None:
    recorder = Recorder()

    class Echo:
        async def stream(self, messages):
            yield "x"

    create_app(Settings(), executor=LemonadeAgentExecutor(Echo()), telemetry=recorder.telemetry)

    values = [p.value for _, p in recorder.points("lemonade_a2a.compat.status")]
    assert values == [2]


# --- the capabilities endpoint and card extension -------------------------------------------------------


def _client(**settings) -> TestClient:
    class Echo:
        async def stream(self, messages):
            yield "x"

    return TestClient(create_app(Settings(**settings), executor=LemonadeAgentExecutor(Echo())))


def test_capabilities_endpoint_is_off_by_default() -> None:
    client = _client()

    assert client.get(CAPABILITIES_PATH).status_code == 404
    assert CAPABILITIES_EXTENSION not in json.dumps(
        client.get("/.well-known/agent-card.json").json()
    )


def test_capabilities_endpoint_describes_the_adapter() -> None:
    client = _client(expose_capabilities=True, features="+adapter.cancel_on_disconnect")

    response = client.get(CAPABILITIES_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["adapter"] == "0.1.0" and body["spec_version"] == "0.1"
    assert body["components"]["a2a_sdk"] and body["compat"]["mode"] == "warn"
    state = {f["id"]: f["active"] for f in body["features"]}
    assert state["adapter.cancel_on_disconnect"] is True and state["a2a.binding.grpc"] is False
    assert "SECRET" not in response.text


def test_capabilities_endpoint_needs_credentials_when_auth_is_on() -> None:
    client = _client(expose_capabilities=True, api_key="k")

    assert client.get(CAPABILITIES_PATH).status_code == 401
    assert client.get(CAPABILITIES_PATH, headers={"Authorization": "Bearer k"}).status_code == 200


def test_card_points_at_it_with_an_optional_extension() -> None:
    card = (
        _client(expose_capabilities=True, public_url="http://localhost:9100")
        .get("/.well-known/agent-card.json")
        .json()
    )

    extensions = card["capabilities"]["extensions"]
    assert [e["uri"] for e in extensions] == [CAPABILITIES_EXTENSION]
    assert not extensions[0].get("required")
    assert extensions[0]["params"]["url"] == "http://localhost:9100" + CAPABILITIES_PATH
    assert extensions[0]["params"]["specVersion"] == "0.1"


# --- feature flag: streaming -----------------------------------------------------------------------------------


def test_switching_streaming_off_changes_the_card_and_the_behaviour() -> None:
    client = _client(features="-a2a.streaming")
    body = json.dumps(
        {"message": {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "hi"}]}}
    )

    assert client.get("/.well-known/agent-card.json").json()["capabilities"]["streaming"] is False
    refused = client.post("/message:stream", headers=HEADERS, content=body)
    assert refused.status_code == 400
    assert refused.json()["error"]["details"][0]["reason"] == "UNSUPPORTED_OPERATION"
    rpc = client.post(
        "/",
        headers=HEADERS,
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendStreamingMessage",
                "params": json.loads(body),
            }
        ),
    ).json()
    assert rpc["error"]["code"] == -32004
    # Blocking sends are unaffected.
    assert client.post("/message:send", headers=HEADERS, content=body).status_code == 200


# --- plugins in doctor ---------------------------------------------------------------------------------


def test_doctor_lists_installed_plugins(installed) -> None:
    from lemonade_a2a.plugins import plugin

    installed("backends", FakeEntryPoint("shout", plugin(lambda s, t: None)))

    checks = compat.check_plugins(Settings())

    assert [(c.verdict, "shout" in c.detail) for c in checks] == [(PASS, True)]
    assert "acme-plugin 2.0.0" in checks[0].detail


def test_doctor_fails_a_plugin_for_another_api_version(installed) -> None:
    old = lambda s, t: None
    old.api_version = 0
    installed("backends", FakeEntryPoint("old", old))

    assert [c.verdict for c in compat.check_plugins(Settings())] == [FAIL]


def test_doctor_warns_about_a_plugin_that_will_not_load(installed) -> None:
    installed("task_stores", FakeEntryPoint("broken", None, fail=True))

    assert [c.verdict for c in compat.check_plugins(Settings())] == [WARN]


def test_a_selected_plugin_that_is_not_installed_fails(installed) -> None:
    checks = compat.check_plugins(Settings(backend="ghost", task_store="ghost2"))

    assert {c.id for c in checks} == {"plugin.backends", "plugin.task_stores"}
    assert {c.verdict for c in checks} == {FAIL}


def test_no_plugins_means_no_noise() -> None:
    assert compat.check_plugins(Settings()) == []


# --- the SDK's own tracing is off by default in `serve` ---------------------------------------------------


def test_serve_turns_the_sdk_spans_off_unless_told_otherwise(monkeypatch) -> None:
    monkeypatch.delenv(cli.SDK_TRACING_VAR, raising=False)
    monkeypatch.setattr("lemonade_a2a.server.serve", lambda settings: None)

    cli.main(["serve"])

    assert cli.os.environ[cli.SDK_TRACING_VAR] == "false"


def test_an_explicit_choice_is_never_overridden(monkeypatch) -> None:
    monkeypatch.setenv(cli.SDK_TRACING_VAR, "true")

    assert cli.default_sdk_tracing() is False
    assert cli.os.environ[cli.SDK_TRACING_VAR] == "true"


def test_the_default_is_applied_once_per_process(monkeypatch) -> None:
    monkeypatch.delenv(cli.SDK_TRACING_VAR, raising=False)

    assert cli.default_sdk_tracing() is True
    assert cli.default_sdk_tracing() is False
