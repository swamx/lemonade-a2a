"""The ``lemonade-a2a`` command: serve (default), doctor, capabilities, config, support-bundle.

Everything except ``serve`` is local and read-only: it talks only to the Lemonade and
adapter endpoints you point it at, never to the project or the internet. See
docs/specification.md.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, compat, registry, sdk_gap
from .config import Settings

MARK = {
    compat.PASS: "[ ok ]",
    compat.WARN: "[warn]",
    compat.FAIL: "[FAIL]",
    compat.UNKNOWN: "[ ?? ]",
}


def _emit(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _dump(value: Any) -> None:
    _emit(json.dumps(value, indent=2, default=str))


# ----- settings helpers --------------------------------------------------------------


def _cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    names = ("host", "port", "profile", "compat", "features", "log_format")
    return {name: getattr(args, name) for name in names if getattr(args, name, None) is not None}


def _load(args: argparse.Namespace) -> tuple[Settings, dict[str, str]]:
    try:
        return Settings.load(config_file=args.config, overrides=_cli_overrides(args))
    except (ValueError, TypeError) as exc:
        sys.stderr.write(f"configuration error: {exc}\n")
        raise SystemExit(compat.EXIT_FAIL) from exc


# ----- doctor ------------------------------------------------------------------------


def _print_checks(title: str, checks: list[compat.Check]) -> None:
    _emit(title)
    for check in checks:
        _emit(f"  {MARK[check.verdict]} {check.title}: {check.detail}")
        if check.hint and check.verdict != compat.PASS:
            _emit(f"         next: {check.hint}")
    _emit()


def cmd_doctor(args: argparse.Namespace) -> int:
    settings, _ = _load(args)
    manifest = registry.manifest()
    local = compat.local_checks(settings, manifest)
    backend = [] if args.no_lemonade else compat.probe_lemonade(settings, manifest, deep=args.deep)
    adapter = compat.probe_adapter(args.adapter_url, settings, manifest) if args.adapter_url else []
    checks = [*local, *backend, *adapter]
    code = compat.exit_code(checks, strict=args.strict)
    gaps = sdk_gap.probe() if args.sdk_gap else None

    if args.json:
        report: dict[str, Any] = {
            "verdict": compat.overall(checks, strict=args.strict),
            "exit_code": code,
            "strict": args.strict,
            "spec_version": registry.registry().get("spec_version"),
            "checks": [check.as_dict() for check in checks],
        }
        if gaps is not None:
            report["sdk_gap"] = [gap.as_dict() for gap in gaps]
        _dump(report)
        return code

    _emit(f"lemonade-a2a {__version__} doctor (spec {registry.registry().get('spec_version')})\n")
    _print_checks("Installed components", local)
    if backend:
        _print_checks("Lemonade", backend)
    if adapter:
        _print_checks("Running adapter", adapter)
    if gaps is not None:
        _emit("What the installed a2a-sdk offers beyond what the adapter uses")
        for gap in gaps:
            if gap.available:
                _emit(
                    f"  - {gap.title}: adapter {gap.adapter}"
                    + (f" ({gap.note})" if gap.note else "")
                )
        _emit()
    verdict = compat.overall(checks, strict=args.strict).upper()
    _emit(f"Verdict: {verdict} (exit {code}{', strict' if args.strict else ''})")
    return code


# ----- capabilities --------------------------------------------------------------------


def cmd_capabilities(args: argparse.Namespace) -> int:
    settings, _ = _load(args)
    resolved = registry.resolve(settings)
    if args.json:
        _dump(
            {
                "spec_version": registry.registry().get("spec_version"),
                "adapter": __version__,
                "features": [
                    {
                        "id": r.id,
                        "state": r.state,
                        "active": r.active,
                        "config": r.config,
                        "verified_by": list(r.verified_by),
                    }
                    for r in resolved
                ],
            }
        )
        return 0
    width = max(len(r.id) for r in resolved)
    _emit(f"Capabilities for this configuration (profile {settings.profile})\n")
    for r in resolved:
        if r.active is None and not args.all:
            continue
        flag = {True: "on ", False: "off", None: "-- "}[r.active]
        if r.state == "unsupported" and not args.all:
            continue
        _emit(
            f"  {flag} {r.id.ljust(width)}  {r.title}"
            + (f"  [{r.config}]" if r.config and r.active is False else "")
        )
    _emit(
        "\n(on = in effect, off = supported but not enabled; --all adds unsupported and observed items)"
    )
    return 0


# ----- config ----------------------------------------------------------------------------


def cmd_config(args: argparse.Namespace) -> int:
    if args.action == "schema":
        _dump(Settings.json_schema())
        return 0
    settings, sources = _load(args)
    if args.action == "validate":
        warnings = [c for c in compat.check_config(settings) if c.verdict != compat.PASS]
        for check in warnings:
            _emit(f"warning: {check.detail}")
        _emit("configuration is valid")
        return 0
    values = settings.as_dict(redact=True)
    if args.json:
        _dump(
            {
                name: {"value": values[name], "source": sources.get(name, "default")}
                for name in values
            }
        )
        return 0
    width = max(len(name) for name in values)
    for name, value in values.items():
        _emit(f"{name.ljust(width)}  {str(value)!s:<40}  ({sources.get(name, 'default')})")
    return 0


# ----- support bundle ---------------------------------------------------------------------


def _scrub(text: str) -> str:
    """Remove the user's home directory and name from collected text."""
    home = Path.home()
    for variant in {str(home), str(home).replace("\\", "/"), str(home).replace("\\", "\\\\")}:
        text = text.replace(variant, "<home>")
    return text.replace(home.name, "<user>") if home.name else text


def build_bundle(settings: Settings, log: Path | None = None, *, deep: bool = False) -> bytes:
    """A redacted zip: versions, effective config, doctor output, features, Lemonade info.

    Never contains prompts, responses or keys: the adapter does not record the first two,
    and secrets are replaced before anything is written.
    """
    from . import diagnostics

    manifest = registry.manifest()
    checks = [
        *compat.local_checks(settings, manifest),
        *compat.probe_lemonade(settings, manifest, deep=deep),
    ]
    parts = {
        "diagnostics.json": diagnostics.collect(settings, log_path=log),
        "doctor.json": {
            "verdict": compat.overall(checks),
            "checks": [check.as_dict() for check in checks],
        },
        "settings.json": settings.as_dict(redact=True),
        "features.json": [
            r.__dict__ | {"verified_by": list(r.verified_by)} for r in registry.resolve(settings)
        ],
        "manifest.json": manifest,
        "README.txt": (
            "lemonade-a2a support bundle\n"
            f"created {datetime.now(UTC).isoformat(timespec='seconds')} by lemonade-a2a {__version__}\n"
            "Secrets are redacted and the home directory is replaced; prompts and responses are "
            "never recorded. Review the files before sharing.\n"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            text = (
                content if isinstance(content, str) else json.dumps(content, indent=2, default=str)
            )
            archive.writestr(name, _scrub(text))
    return buffer.getvalue()


def cmd_support_bundle(args: argparse.Namespace) -> int:
    settings, _ = _load(args)
    output = Path(args.output or f"lemonade-a2a-support-{datetime.now(UTC):%Y%m%d-%H%M%S}.zip")
    output.write_bytes(build_bundle(settings, args.log, deep=args.deep))
    _emit(f"wrote {output} ({output.stat().st_size} bytes); review it before sharing")
    return 0


# ----- serve / version ----------------------------------------------------------------------


SDK_TRACING_VAR = "OTEL_INSTRUMENTATION_A2A_SDK_ENABLED"


def default_sdk_tracing() -> bool:
    """Turn the A2A SDK's own OpenTelemetry spans off unless the operator said otherwise.

    The SDK wraps its request handling in tracing decorators that cost time on every request
    *even when no telemetry is configured*, because the OpenTelemetry API is installed. Measured
    on the mock backend: about 2-5 ms per request in a warm sequential benchmark and up to
    about 16 ms for requests that arrive in bursts after idle (docs/benchmarks.md). The
    adapter's own spans (request, task, Lemonade call) cover the same ground. The SDK reads the switch once, when it is first
    imported, so this must run before ``lemonade_a2a.server`` is imported. Set
    ``OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=true`` to get the SDK's spans back (they carry the
    JSON-RPC method names). Returns whether the default was applied.
    """
    if SDK_TRACING_VAR in os.environ:
        return False
    os.environ[SDK_TRACING_VAR] = "false"
    return True


def cmd_serve(args: argparse.Namespace) -> int:
    settings, _ = _load(args)
    default_sdk_tracing()
    from .server import serve

    serve(settings)
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    _emit(f"lemonade-a2a {__version__}")
    _emit(f"a2a-sdk {compat.installed_version('a2a-sdk') or 'not installed'}")
    _emit(f"specification {registry.registry().get('spec_version')}")
    return 0


# ----- parser -------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lemonade-a2a", description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="TOML config file (or LEMONADE_A2A_CONFIG)")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the adapter (the default)")
    for flag in ("host", "profile", "compat", "features", "log-format"):
        serve.add_argument(f"--{flag}", dest=flag.replace("-", "_"))
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", help="cross-validate versions, features and the backend")
    doctor.add_argument("--deep", action="store_true", help="also run a tiny streaming generation")
    doctor.add_argument("--adapter-url", help="also check a running adapter's Agent Card")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--strict", action="store_true", help="treat warnings as failures")
    doctor.add_argument(
        "--sdk-gap", action="store_true", help="list SDK capabilities the adapter does not use"
    )
    doctor.add_argument("--no-lemonade", action="store_true", help="skip the Lemonade probe")
    doctor.set_defaults(func=cmd_doctor)

    caps = sub.add_parser("capabilities", help="the feature registry for this configuration")
    caps.add_argument("--json", action="store_true")
    caps.add_argument("--all", action="store_true", help="include unsupported and observed items")
    caps.set_defaults(func=cmd_capabilities)

    config = sub.add_parser("config", help="show, validate or describe the configuration")
    config.add_argument("action", choices=["show", "validate", "schema"])
    config.add_argument("--json", action="store_true")
    config.set_defaults(func=cmd_config)

    bundle = sub.add_parser("support-bundle", help="write a redacted diagnostics archive")
    bundle.add_argument("--output")
    bundle.add_argument("--log", type=Path, help="adapter log file to include (tail only)")
    bundle.add_argument("--deep", action="store_true")
    bundle.set_defaults(func=cmd_support_bundle)

    version = sub.add_parser("version", help="print versions")
    version.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    commands = {
        "serve",
        "doctor",
        "capabilities",
        "config",
        "support-bundle",
        "version",
        "-h",
        "--help",
    }
    # `lemonade-a2a` and `lemonade-a2a --port 9200` keep meaning "serve".
    first_command = next((a for a in argv if not a.startswith("-") and a in commands), None)
    if first_command is None and not any(a in ("-h", "--help") for a in argv):
        argv = (
            ["serve", *argv]
            if not (argv and argv[0] == "--config")
            else [*argv[:2], "serve", *argv[2:]]
        )
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
