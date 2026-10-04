"""Helpers shared by the black-box and real-Lemonade validation scripts."""

from __future__ import annotations


def artifact_text(result: dict | None) -> str:
    """Concatenate text parts of the task's artifacts.

    Only artifacts are read: the task history echoes the user's prompt, which
    must not be mistaken for model output.
    """
    task = (result or {}).get("task") or result or {}
    pieces = []
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            text = part.get("text")
            if isinstance(text, str):
                pieces.append(text)
    return "".join(pieces)
