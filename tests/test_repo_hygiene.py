import subprocess
from pathlib import Path

REPLACEMENT_CHAR = chr(0xFFFD)  # built at runtime so this file does not match itself
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".toml", ".txt"}


def test_tracked_text_files_have_no_stray_control_characters() -> None:
    """Guards against escape sequences mangled by scripted edits (e.g. ``\a`` -> BEL)."""
    root = Path(__file__).resolve().parent.parent
    listing = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False
    )
    bad = []
    for name in listing.stdout.split():
        path = root / name
        if path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any((ord(c) < 32 and c not in "\n\r\t") or c == REPLACEMENT_CHAR for c in text):
            bad.append(name)
    assert not bad, f"control/replacement characters found in: {bad}"
