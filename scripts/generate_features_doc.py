"""Render docs/features.md from the feature registry (the registry is the source of truth).

python scripts/generate_features_doc.py          # rewrite docs/features.md
python scripts/generate_features_doc.py --check  # exit 1 if it is out of date (CI)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lemonade_a2a import registry
from lemonade_a2a.config import Settings

STATE_ORDER = ["supported", "experimental", "unsupported", "deprecated", "observed", "unknown"]
DOMAINS = {
    "a2a": ("A2A protocol", "What the adapter offers to A2A clients."),
    "adapter": ("Adapter", "What `lemonade-a2a` itself provides and how to switch it."),
    "lemonade": ("Lemonade behaviour", "What the adapter relies on from Lemonade Server."),
    "sdk": ("A2A SDK", "What was observed in the `a2a-sdk` it is built on."),
}


def _evidence(references: list[str]) -> str:
    if not references:
        return "-"
    return "<br>".join(f"`{ref}`" for ref in references)


def render() -> str:
    default = {item.id: item for item in registry.resolve(Settings())}
    lines = [
        "# Feature registry",
        "",
        "Generated from [`features.json`](../src/lemonade_a2a/spec/features.json) by "
        "`scripts/generate_features_doc.py`; do not edit by hand. The same data is available "
        "at runtime from `lemonade-a2a capabilities` and, when enabled, from the capabilities "
        f"endpoint. Specification version {registry.registry()['spec_version']}; "
        "see [specification.md](specification.md).",
        "",
        "**State**: `supported` works and has evidence; `unsupported` is deliberately not offered "
        "(and is rejected, not ignored); `observed` is behaviour of a dependency the adapter relies "
        "on or works around; `unknown` is not yet established. **Default** shows whether the feature "
        "is in effect with no configuration. **Switch** is the setting that controls it.",
        "",
    ]
    for domain, (title, blurb) in DOMAINS.items():
        items = [i for i in registry.features() if i["id"].split(".")[0] == domain]
        items.sort(key=lambda i: (STATE_ORDER.index(i["state"]), i["id"]))
        lines += [f"## {title}", "", blurb, ""]
        lines += [
            "| Id | What | State | Default | Switch | Evidence |",
            "|---|---|---|---|---|---|",
        ]
        for item in items:
            active = default[item["id"]].active
            shown = {True: "on", False: "off", None: "-"}[active]
            switch = f"`{item['config']}`" if item.get("config") else "-"
            lines.append(
                f"| `{item['id']}` | {item['title']}"
                + (f"<br><sub>{item['notes']}</sub>" if item.get("notes") else "")
                + f" | {item['state']} | {shown} | {switch} | {_evidence(item['verified_by'])} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "docs" / "features.md"
    text = render()
    if args.check:
        current = (
            target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.exists() else ""
        )
        if current != text:
            sys.stderr.write(
                "docs/features.md is out of date: run scripts/generate_features_doc.py\n"
            )
            return 1
        return 0
    target.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
