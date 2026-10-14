"""Canary: run the test suite (and optionally the TCK) against several ``a2a-sdk`` versions.

    python scripts/canary.py --sdk 1.2.0 --sdk latest --output canary-results.json
    python scripts/canary.py --sdk unbounded          # newest SDK incl. pre-releases, ignoring our pin

Each version gets its own virtual environment, so nothing leaks between runs. ``--sdk``:

* ``X.Y.Z``     that exact release (must be inside the declared range);
* ``latest``    the newest release the declared range allows (what ``pip install -U`` gives);
* ``unbounded`` the newest release or pre-release with the upper bound removed, to see what a
                widening of the range would break.

The JSON it writes feeds ``scripts/generate_manifest.py``. Exit code 1 if any *requested*
version failed (CI decides which failures block).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("check", False)
    return subprocess.run(  # noqa: PLW1510 - check is set through kwargs above
        command, capture_output=True, text=True, encoding="utf-8", **kwargs
    )


def venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def junit_summary(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    attributes = suite.attrib if suite is not None else {}
    return {key: int(attributes.get(key, 0)) for key in ("tests", "failures", "errors", "skipped")}


def install(python: Path, spec: str) -> tuple[bool, str]:
    """Install the adapter with its test dependencies and the requested SDK."""
    if spec == "unbounded":
        steps = [
            [str(python), "-m", "pip", "install", "-q", "-e", str(ROOT), "--no-deps"],
            [str(python), "-m", "pip", "install", "-q", "--pre", "a2a-sdk[fastapi]"],
            [str(python), "-m", "pip", "install", "-q", f"{ROOT}[test]", "--no-deps"],
            [str(python), "-m", "pip", "install", "-q", *test_requirements()],
        ]
    else:
        pin = [] if spec == "latest" else [f"a2a-sdk[fastapi]=={spec}"]
        steps = [[str(python), "-m", "pip", "install", "-q", "-e", f"{ROOT}[test]", *pin]]
    for step in steps:
        result = run(step)
        if result.returncode:
            return False, (result.stderr or result.stdout)[-800:]
    return True, ""


def test_requirements() -> list[str]:
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wanted = [d for d in project["dependencies"] if not d.startswith("a2a-sdk")]
    for extra in ("otel", "sqlite", "test"):
        wanted += [
            d for d in project["optional-dependencies"][extra] if not d.startswith("lemonade-a2a")
        ]
    return wanted


def canary(spec: str, work: Path, tck_dir: Path | None) -> dict:
    environment = work / f"venv-{spec.replace('.', '_')}"
    run([sys.executable, "-m", "venv", str(environment)], check=True)
    python = venv_python(environment)
    ok, why = install(python, spec)
    entry: dict = {"requested": spec, "status": "failed"}
    if not ok:
        entry["reason"] = f"install failed: {why}"
        entry["a2a_sdk"] = spec
        return entry
    version = run([str(python), "-c", "import importlib.metadata as m;print(m.version('a2a-sdk'))"])
    entry["a2a_sdk"] = version.stdout.strip()
    junit = work / f"junit-{entry['a2a_sdk']}.xml"
    tests = run(
        [
            str(python),
            "-m",
            "pytest",
            "tests",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            "--ignore=tests/integration",
        ],
        cwd=ROOT,
    )
    entry["tests"] = junit_summary(junit) if junit.exists() else {}
    doctor = run([str(python), "-m", "lemonade_a2a", "doctor", "--no-lemonade", "--json"], cwd=ROOT)
    try:
        entry["doctor"] = json.loads(doctor.stdout)["verdict"]
    except (ValueError, KeyError):
        entry["doctor"] = "error"
    passed = tests.returncode == 0
    if passed and tck_dir:
        # Official run (only the two known deviations allowed), then the run with the messageId
        # workaround (must be completely clean), exactly as .github/workflows/tck.yml does.
        for label, extra in (
            ("tck", []),
            ("tck_patched", ["--patch", "tck/patches/unique-message-ids.patch"]),
        ):
            output = work / f"{label}-{entry['a2a_sdk']}.json"
            result = run(
                [
                    str(python),
                    "scripts/run_tck.py",
                    "--tck-dir",
                    str(tck_dir),
                    "--output",
                    str(output),
                    *extra,
                ],
                cwd=ROOT,
            )
            entry[label] = (
                json.loads(output.read_text("utf-8"))["status"] if output.exists() else "error"
            )
            passed = passed and result.returncode == 0
    entry["status"] = "passed" if passed else "failed"
    if not passed:
        entry["reason"] = (tests.stdout or tests.stderr)[-600:]
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sdk", action="append", required=True)
    parser.add_argument("--tck-dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("canary-results.json"))
    parser.add_argument("--keep", action="store_true", help="keep the virtual environments")
    args = parser.parse_args()

    results = []
    with tempfile.TemporaryDirectory() as temp:
        for spec in args.sdk:
            sys.stdout.write(f"== a2a-sdk {spec}\n")
            sys.stdout.flush()
            results.append(canary(spec, Path(temp), args.tck_dir))
            last = results[-1]
            sys.stdout.write(f"   {last['a2a_sdk']}: {last['status']} {last.get('tests', '')}\n")
    report = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "results": results,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if all(r["status"] == "passed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
