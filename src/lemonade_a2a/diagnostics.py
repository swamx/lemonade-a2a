"""Environment and service diagnostics for bug reports and failed validation runs.

``collect`` gathers what is needed to reproduce a problem (versions, platform,
redacted settings, what the Lemonade server reports about itself) without ever
including secrets or prompt content. Every probe is best-effort: a failing probe
is recorded as an error string, never raised, so diagnostics work precisely when
something is broken.

Command line:  python -m lemonade_a2a.diagnostics [--lemonade-url URL] [--log FILE]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

from .config import Settings

PACKAGES = ("lemonade-a2a", "a2a-sdk", "fastapi", "starlette", "uvicorn", "httpx", "pydantic")
SECRET_MARKERS = ("key", "secret", "token", "password")
LOG_TAIL_LINES = 200


def _redact(settings: Settings) -> dict[str, Any]:
    values = dataclasses.asdict(settings)
    for name, value in values.items():
        if value and any(marker in name for marker in SECRET_MARKERS):
            values[name] = "***set***"
    return values


def _versions() -> dict[str, str]:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = "not installed"
    return found


def _get_json(client: httpx.Client, url: str) -> Any:
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _lemonade(base_url: str, api_key: str, timeout: float) -> dict[str, Any]:
    root = base_url.rstrip("/").removesuffix("/v1")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        models = _get_json(client, f"{root}/v1/models")
        ids = (
            [m.get("id") for m in models.get("data", [])]
            if isinstance(models, dict) and "data" in models
            else models
        )
        return {
            "base_url": base_url,
            "health": _get_json(client, f"{root}/api/v1/health"),
            "system_info": _get_json(client, f"{root}/api/v1/system-info"),
            "models": ids,
        }


def _log_tail(path: Path | None) -> list[str] | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_TAIL_LINES:]
    except OSError as exc:
        return [f"could not read {path}: {exc}"]


def collect(
    settings: Settings | None = None, *, log_path: Path | None = None, timeout: float = 5.0
) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    return {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": _versions(),
        "settings": _redact(settings),
        "lemonade": _lemonade(settings.lemonade_base_url, settings.lemonade_api_key, timeout),
        "adapter_log_tail": _log_tail(log_path),
    }


def write(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lemonade-url", help="override LEMONADE_BASE_URL")
    parser.add_argument("--log", type=Path, help="adapter log file to include (tail only)")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.lemonade_url:
        settings = dataclasses.replace(settings, lemonade_base_url=args.lemonade_url)
    print(json.dumps(collect(settings, log_path=args.log), indent=2, default=str))


if __name__ == "__main__":
    main()
