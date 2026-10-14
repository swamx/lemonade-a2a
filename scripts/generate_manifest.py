"""Generate the compatibility manifest (src/lemonade_a2a/spec/compat.json) from CI results.

The manifest records what was *tested*, so it is generated, never edited by hand:

    python scripts/generate_manifest.py \\
        --canary canary-results.json \\
        --tck docs/conformance-results.json \\
        --lemonade 2026.40.0 --python 3.11 3.12 3.13

``--canary`` is the JSON written by ``scripts/canary.py`` (one entry per a2a-sdk version).
Versions that passed are listed as tested; versions that failed become ``known_broken``.
Without ``--canary`` only the currently installed a2a-sdk is recorded, as untested-by-matrix.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "src" / "lemonade_a2a" / "spec" / "compat.json"


def declared_sdk_range() -> str:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = next(d for d in pyproject["project"]["dependencies"] if d.startswith("a2a-sdk"))
    return requirement.split("]", 1)[1]


def build(args: argparse.Namespace) -> dict:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    tested: list[str] = []
    broken: list[dict] = []
    evidence: dict = {}
    if args.canary:
        results = json.loads(Path(args.canary).read_text(encoding="utf-8"))
        for item in sorted(results["results"], key=lambda r: r["a2a_sdk"]):
            if item["status"] == "passed":
                tested.append(item["a2a_sdk"])
            else:
                broken.append(
                    {
                        "range": f"=={item['a2a_sdk']}",
                        "reason": item.get("reason", "canary failed"),
                    }
                )
        evidence["canary"] = {
            "run_at": results.get("run_at"),
            "python": results.get("python"),
            "results": results["results"],
        }
    else:
        tested = [metadata.version("a2a-sdk")]
    if args.tck:
        tck = json.loads(Path(args.tck).read_text(encoding="utf-8"))
        evidence["tck"] = {
            "commit": tck.get("tck_commit"),
            "status": tck.get("status"),
            "test_cases": tck.get("test_cases"),
            "run_at": tck.get("run_at"),
        }
    return {
        "schema_version": 1,
        "spec_version": json.loads(
            (ROOT / "src/lemonade_a2a/spec/features.json").read_text(encoding="utf-8")
        )["spec_version"],
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "adapter": {"version": pyproject["project"]["version"]},
        "python": {
            "tested": args.python,
            "minimum": pyproject["project"]["requires-python"].lstrip(">="),
        },
        "a2a_protocol": {"supported": ["1.0"]},
        "a2a_sdk": {"declared": declared_sdk_range(), "tested": tested, "known_broken": broken},
        "lemonade": {"tested": args.lemonade, "minimum": args.lemonade_minimum or args.lemonade[0]},
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--canary", help="JSON from scripts/canary.py")
    parser.add_argument("--tck", help="docs/conformance-results.json")
    parser.add_argument("--lemonade", nargs="+", required=True, help="tested Lemonade versions")
    parser.add_argument("--lemonade-minimum")
    parser.add_argument("--python", nargs="+", default=["3.11", "3.12", "3.13"])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    manifest = build(args)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.output}: sdk tested {manifest['a2a_sdk']['tested']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
