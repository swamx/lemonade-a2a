"""Compatibility checks shared by ``doctor`` and the startup gate (docs/specification.md §5, §7).

A check compares what is installed or reachable with what the manifest says was tested,
and returns a verdict with a reason and a next step. Nothing here changes any state.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Any

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from . import __version__, registry
from .config import Settings

PASS, WARN, FAIL, UNKNOWN = "pass", "warn", "fail", "unknown"
EXIT_OK, EXIT_WARN, EXIT_FAIL, EXIT_UNKNOWN = 0, 1, 2, 3


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    verdict: str
    detail: str = ""
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def exit_code(checks: list[Check], *, strict: bool = False) -> int:
    """fail → 2, else unknown → 3, else warn → 1 (2 under ``strict``), else 0."""
    verdicts = {check.verdict for check in checks}
    if FAIL in verdicts or (strict and WARN in verdicts):
        return EXIT_FAIL
    if UNKNOWN in verdicts:
        return EXIT_UNKNOWN
    return EXIT_WARN if WARN in verdicts else EXIT_OK


def overall(checks: list[Check], *, strict: bool = False) -> str:
    return {EXIT_OK: PASS, EXIT_WARN: WARN, EXIT_FAIL: FAIL, EXIT_UNKNOWN: UNKNOWN}[
        exit_code(checks, strict=strict)
    ]


def installed_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def _parse(version: str | None) -> Version | None:
    try:
        return Version(version) if version else None
    except InvalidVersion:
        return None


# ----- component versions ------------------------------------------------------------


def check_python(manifest: dict[str, Any]) -> Check:
    current = f"{sys.version_info.major}.{sys.version_info.minor}"
    tested = manifest.get("python", {}).get("tested", [])
    minimum = manifest.get("python", {}).get("minimum", "3.11")
    detail = f"Python {platform.python_version()}"
    if current in tested:
        return Check("python", "Python version", PASS, detail)
    if tuple(map(int, current.split("."))) >= tuple(map(int, minimum.split("."))):
        return Check(
            "python",
            "Python version",
            WARN,
            f"{detail} is supported but untested (tested: {', '.join(tested)})",
            "Use a tested Python for production.",
        )
    return Check("python", "Python version", FAIL, f"{detail} is below the minimum {minimum}")


def check_adapter(manifest: dict[str, Any]) -> Check:
    recorded = manifest.get("adapter", {}).get("version")
    if recorded and recorded != __version__:
        return Check(
            "adapter",
            "lemonade-a2a version",
            WARN,
            f"running {__version__}; the manifest was generated for {recorded}",
            "Regenerate the manifest (scripts/generate_manifest.py) or install the matching release.",
        )
    return Check("adapter", "lemonade-a2a version", PASS, f"lemonade-a2a {__version__}")


def check_sdk(manifest: dict[str, Any]) -> Check:
    section = manifest.get("a2a_sdk", {})
    version_text = installed_version("a2a-sdk")
    version = _parse(version_text)
    if version is None:
        return Check("a2a_sdk", "a2a-sdk", FAIL, "a2a-sdk is not installed", "pip install a2a-sdk")
    detail = f"a2a-sdk {version_text}"
    for broken in section.get("known_broken", []):
        if version in SpecifierSet(broken["range"]):
            return Check(
                "a2a_sdk",
                "a2a-sdk",
                FAIL,
                f"{detail} is known broken: {broken.get('reason', '')}",
                f"Install a version outside {broken['range']}.",
            )
    if version_text in section.get("tested", []):
        return Check("a2a_sdk", "a2a-sdk", PASS, f"{detail} (tested)")
    declared = section.get("declared", "")
    try:
        inside = version in SpecifierSet(declared) if declared else False
    except InvalidSpecifier:
        inside = False
    if inside:
        return Check(
            "a2a_sdk",
            "a2a-sdk",
            WARN,
            f"{detail} is inside the declared range {declared} but was not tested "
            f"(tested: {', '.join(section.get('tested', [])) or 'none'})",
            "Run `lemonade-a2a doctor --deep` and the TCK, or pin a tested version.",
        )
    return Check(
        "a2a_sdk",
        "a2a-sdk",
        FAIL,
        f"{detail} is outside the declared range {declared}",
        "pip install 'a2a-sdk" + declared + "'",
    )


def check_lemonade_version(manifest: dict[str, Any], version_text: str | None) -> Check:
    section = manifest.get("lemonade", {})
    version = _parse(version_text)
    if version is None:
        return Check("lemonade_version", "Lemonade version", UNKNOWN, "version not reported")
    detail = f"Lemonade {version_text}"
    minimum = _parse(section.get("minimum"))
    if minimum and version < minimum:
        return Check(
            "lemonade_version",
            "Lemonade version",
            FAIL,
            f"{detail} is older than the minimum {section['minimum']}",
            "Upgrade Lemonade Server.",
        )
    tested = section.get("tested", [])
    if version_text in tested:
        return Check("lemonade_version", "Lemonade version", PASS, f"{detail} (tested)")
    newest = max((_parse(item) for item in tested if _parse(item)), default=None)
    if newest and version > newest:
        return Check(
            "lemonade_version",
            "Lemonade version",
            WARN,
            f"{detail} is newer than the newest tested ({max(tested, key=_parse)})",
            "Run `lemonade-a2a doctor --deep`; report results if it works.",
        )
    return Check(
        "lemonade_version", "Lemonade version", WARN, f"{detail} was not tested", "Run --deep."
    )


# ----- local configuration -----------------------------------------------------------


def check_registry() -> Check:
    problems = registry.validate()
    if problems:
        return Check("registry", "Feature registry", FAIL, "; ".join(problems[:3]))
    return Check("registry", "Feature registry", PASS, f"{len(registry.features())} features")


def check_config(settings: Settings) -> list[Check]:
    from .server import startup_warnings

    checks = [Check("config", "Configuration", PASS, f"profile {settings.profile}")]
    for warning in startup_warnings(settings):
        checks.append(Check("config.warning", "Configuration", WARN, warning))
    if settings.otel_enabled:
        checks.append(check_telemetry(settings))
    return checks


def check_telemetry(settings: Settings) -> Check:
    missing = [
        name
        for name in ("opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-http")
        if installed_version(name) is None
    ]
    if missing:
        return Check(
            "telemetry",
            "OpenTelemetry",
            FAIL,
            f"enabled but {', '.join(missing)} is not installed",
            "pip install 'lemonade-a2a[otel]'",
        )
    return Check(
        "telemetry",
        "OpenTelemetry",
        PASS,
        f"capture {settings.otel_capture}, prometheus {'on' if settings.otel_prometheus else 'off'}",
    )


def check_plugins(settings: Settings) -> list[Check]:
    """Installed third-party plugins, and whether the ones the settings select can be used."""
    from . import builtin, plugins

    checks: list[Check] = []
    for info in plugins.discover_all():
        label = f"{info.group.removeprefix('lemonade_a2a.')} plugin {info.name} ({info.distribution} {info.version})"
        if info.status == "ok":
            checks.append(
                Check("plugin", "Plugin", PASS, f"{label}, extension API {info.api_version}")
            )
        elif info.status == "api_mismatch":
            checks.append(
                Check(
                    "plugin",
                    "Plugin",
                    FAIL,
                    f"{label}: {info.detail}",
                    "Upgrade the plugin or install a lemonade-a2a that provides its API version.",
                )
            )
        else:
            checks.append(Check("plugin", "Plugin", WARN, f"{label} failed to load: {info.detail}"))
    selected = {
        "backends": (settings.backend, builtin.BACKENDS),
        "task_stores": (settings.task_store, builtin.TASK_STORES),
        "authenticators": (settings.authenticator, builtin.AUTHENTICATORS),
        "telemetry": (settings.telemetry_plugin, builtin.TELEMETRY),
    }
    for group, (name, built_in) in selected.items():
        if not name or name in built_in:
            continue
        usable = any(
            i.name == name and i.status == "ok" for i in plugins.discover(plugins.GROUPS[group])
        )
        if not usable:
            checks.append(
                Check(
                    f"plugin.{group}",
                    "Selected plugin",
                    FAIL,
                    f"{group} plugin {name!r} is selected but not installed or not usable",
                    "Install the package that provides it, or unset the setting.",
                )
            )
    return checks


def local_checks(settings: Settings, manifest: dict[str, Any] | None = None) -> list[Check]:
    manifest = manifest if manifest is not None else registry.manifest()
    return [
        check_python(manifest),
        check_adapter(manifest),
        check_sdk(manifest),
        check_registry(),
        *check_config(settings),
        *check_plugins(settings),
    ]


# ----- backend and adapter probes ------------------------------------------------------


def server_root(base_url: str) -> str:
    """``http://host:13305/v1`` → ``http://host:13305``."""
    root = base_url.rstrip("/")
    return root.removesuffix("/v1")


def probe_lemonade(
    settings: Settings,
    manifest: dict[str, Any] | None = None,
    *,
    deep: bool = False,
    timeout: float = 5.0,
) -> list[Check]:
    manifest = manifest if manifest is not None else registry.manifest()
    root = server_root(settings.lemonade_base_url)
    checks: list[Check] = []
    try:
        health = httpx.get(f"{root}/api/v1/health", timeout=timeout)
        health.raise_for_status()
        data = health.json()
    except (httpx.HTTPError, ValueError) as exc:
        reason = type(exc).__name__
        return [
            Check(
                "lemonade",
                "Lemonade reachable",
                UNKNOWN,
                f"{root} did not answer ({reason})",
                "Start Lemonade Server or set LEMONADE_BASE_URL.",
            )
        ]
    checks.append(
        Check("lemonade", "Lemonade reachable", PASS, f"{root} status {data.get('status')}")
    )
    checks.append(check_lemonade_version(manifest, data.get("version")))

    try:
        models = httpx.get(f"{root}/v1/models", timeout=timeout).json().get("data", [])
    except (httpx.HTTPError, ValueError):
        models = []
    names = [item.get("id") for item in models]
    if settings.model and settings.model not in names:
        checks.append(
            Check(
                "lemonade_model",
                "Configured model",
                FAIL,
                f"{settings.model!r} is not in Lemonade's model list",
                "Check LEMONADE_MODEL against `lemonade list`.",
            )
        )
    elif settings.model:
        context = next(
            (m.get("context_length") for m in models if m.get("id") == settings.model), None
        )
        detail = f"{settings.model}" + (f", context window {context} tokens" if context else "")
        small = isinstance(context, int) and context < 4096
        checks.append(
            Check(
                "lemonade_model",
                "Configured model",
                WARN if small else PASS,
                detail,
                "A small context window rejects long prompts (the task fails with a clear message)."
                if small
                else "",
            )
        )
    else:
        checks.append(
            Check(
                "lemonade_model",
                "Configured model",
                PASS,
                f"none set; Lemonade will use its default ({len(names)} models listed)",
            )
        )

    if deep:
        checks.append(_probe_generation(settings, root, timeout=max(timeout, 60.0)))
    return checks


def _probe_generation(settings: Settings, root: str, *, timeout: float) -> Check:
    if not settings.model:
        return Check(
            "lemonade_stream",
            "Streaming generation",
            UNKNOWN,
            "no model configured",
            "Set LEMONADE_MODEL to probe generation.",
        )
    body = {
        "model": settings.model,
        "messages": [{"role": "user", "content": "Say hi."}],
        "stream": True,
        "max_tokens": 16,
    }
    chunks = reasoning = 0
    try:
        with httpx.stream(
            "POST", f"{root}/v1/chat/completions", json=body, timeout=timeout
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:") or "[DONE]" in line:
                    continue
                event = httpx.Response(200, content=line[5:]).json()
                if event.get("error"):
                    return Check(
                        "lemonade_stream",
                        "Streaming generation",
                        FAIL,
                        f"error inside the stream: {event['error'].get('type', 'unknown')}",
                    )
                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                chunks += bool(delta.get("content"))
                reasoning += bool(delta.get("reasoning_content"))
    except (httpx.HTTPError, ValueError) as exc:
        return Check("lemonade_stream", "Streaming generation", FAIL, type(exc).__name__)
    if chunks:
        return Check("lemonade_stream", "Streaming generation", PASS, f"{chunks} answer chunks")
    if reasoning:
        return Check(
            "lemonade_stream",
            "Streaming generation",
            WARN,
            "only reasoning chunks within 16 tokens (a reasoning model)",
            "Normal for reasoning models; the adapter handles reasoning_content.",
        )
    return Check("lemonade_stream", "Streaming generation", FAIL, "no text chunks")


def probe_adapter(
    url: str, settings: Settings, manifest: dict[str, Any] | None = None, *, timeout: float = 5.0
) -> list[Check]:
    """Compare a *running* adapter's Agent Card with the registry and the manifest."""
    manifest = manifest if manifest is not None else registry.manifest()
    url = url.rstrip("/")
    try:
        card = httpx.get(f"{url}/.well-known/agent-card.json", timeout=timeout).json()
    except (httpx.HTTPError, ValueError):
        return [
            Check(
                "adapter_card",
                "Running adapter",
                UNKNOWN,
                f"no Agent Card at {url}",
                "Start the adapter or pass --adapter-url.",
            )
        ]
    checks = [
        Check("adapter_card", "Running adapter", PASS, f"{card.get('name')} {card.get('version')}")
    ]
    supported = set(manifest.get("a2a_protocol", {}).get("supported", []))
    versions = {i.get("protocolVersion") for i in card.get("supportedInterfaces", [])}
    if supported and not versions <= supported:
        checks.append(
            Check(
                "protocol",
                "Protocol versions",
                FAIL,
                f"card advertises {sorted(v for v in versions if v)}; tested {sorted(supported)}",
            )
        )
    else:
        checks.append(
            Check(
                "protocol", "Protocol versions", PASS, ", ".join(sorted(v for v in versions if v))
            )
        )

    by_id = {item["id"]: item for item in registry.features()}
    declared = card.get("capabilities", {})
    mismatches = []
    if (
        declared.get("pushNotifications")
        and by_id["a2a.push_notifications"]["state"] != "supported"
    ):
        mismatches.append("pushNotifications declared but unsupported")
    if declared.get("streaming") and not registry.is_active(by_id["a2a.streaming"], settings):
        mismatches.append("streaming declared but switched off in this configuration")
    if (
        declared.get("extendedAgentCard")
        and by_id["a2a.extended_agent_card"]["state"] != "supported"
    ):
        mismatches.append("extendedAgentCard declared but unsupported")
    checks.append(
        Check(
            "card_registry",
            "Card versus registry",
            FAIL if mismatches else PASS,
            "; ".join(mismatches) or "every declared capability is supported",
        )
    )
    return checks
