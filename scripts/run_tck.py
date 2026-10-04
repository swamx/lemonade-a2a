"""Run the official A2A TCK against the Lemonade A2A protocol surface.

Starts ``tck.tck_sut`` (the real server layer plus a scenario executor, see its
docstring), runs a local clone of https://github.com/a2aproject/a2a-tck against
it, and records the outcome in ``docs/conformance-results.json``.

    git clone https://github.com/a2aproject/a2a-tck && cd a2a-tck
    uv venv && uv pip install -e .            # TCK's own virtualenv
    cd ../lemonade-a2a
    python scripts/run_tck.py --tck-dir ../a2a-tck
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEVIATION_NOTES = {
    "CORE-HIST-005": "TCK test artifact: follow-up messages reuse one messageId, which the SDK deduplicates",
    "CORE-HIST-006": "TCK test artifact: follow-up messages reuse one messageId, which the SDK deduplicates",
}


def git_sha(path: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or None
    except OSError:
        return None


def venv_python(tck_dir: Path) -> Path:
    scripts = tck_dir / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    return scripts / ("python.exe" if os.name == "nt" else "python")


def wait_for_card(url: str, attempts: int = 60) -> None:
    for _ in range(attempts):
        try:
            urllib.request.urlopen(f"{url}/.well-known/agent-card.json", timeout=2)
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"SUT did not become ready at {url}")


def summarize(tck_dir: Path) -> dict:
    report = json.loads((tck_dir / "reports" / "compatibility.json").read_text(encoding="utf-8"))
    transports: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"passed": 0, "failed": 0, "skipped": 0}
    )
    for requirement in report["per_requirement"].values():
        for transport, status in requirement["transports"].items():
            bucket = {"PASS": "passed", "FAIL": "failed"}.get(status, "skipped")
            transports[transport][bucket] += 1

    cases = collections.Counter()
    for case in ET.parse(tck_dir / "reports" / "junitreport.xml").getroot().iter("testcase"):
        skipped = case.find("skipped")
        if case.find("failure") is not None or case.find("error") is not None:
            cases["failed"] += 1
        elif skipped is not None:
            # pytest.xfail (SHOULD-level deviations) is reported as a typed skip.
            cases["xfailed" if "xfail" in (skipped.get("type") or "") else "skipped"] += 1
        else:
            cases["passed"] += 1
    deviations = sorted(
        f"{req_id} ({req['level']}) on {', '.join(t for t, st in req['transports'].items() if st == 'FAIL')}"
        for req_id, req in report["per_requirement"].items()
        if "FAIL" in req["transports"].values()
    )
    return {
        "known_deviations": deviations,
        "tck_summary": report["summary"],
        "requirements_by_transport": dict(transports),
        "test_cases": dict(cases),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tck-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "conformance-results.json")
    args = parser.parse_args()
    tck_dir = args.tck_dir.resolve()
    url = f"http://127.0.0.1:{args.port}"

    env = {**os.environ, "PYTHONPATH": str(ROOT), "TCK_SUT_PORT": str(args.port)}
    sut = subprocess.Popen([sys.executable, "-m", "tck.tck_sut"], cwd=ROOT, env=env)
    try:
        wait_for_card(url)
        subprocess.run(
            [str(venv_python(tck_dir)), "run_tck.py", "--sut-host", url],
            cwd=tck_dir,
            check=False,
        )
    finally:
        sut.terminate()
        sut.wait(timeout=10)

    summary = summarize(tck_dir)
    results = {
        "schema_version": 2,
        "a2a_protocol": "1.0",
        "implementation_commit": git_sha(ROOT),
        "tck_commit": git_sha(tck_dir),
        "itk_commit": None,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "failed" if summary["test_cases"].get("failed") else "passed",
        "notes": [
            (
                "skipped = capability not declared (gRPC, push notifications, extended card) "
                "or not applicable (streaming agent); "
                "xfailed = SHOULD-level deviations listed above."
            ),
            "overall_compatibility in tck_summary counts skipped requirements as not passing.",
        ],
        "scope": "protocol surface (real server layer + TCK scenario executor); not Lemonade inference",
        "known_deviations": summary["known_deviations"],
        "deviation_notes": DEVIATION_NOTES,
        "transports": summary["requirements_by_transport"],
        "test_cases": summary["test_cases"],
        "tck_summary": summary["tck_summary"],
    }
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 1 if results["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
