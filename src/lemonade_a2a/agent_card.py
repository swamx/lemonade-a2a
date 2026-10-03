from __future__ import annotations

from typing import Any

from .config import Settings


def build_agent_card(settings: Settings) -> dict[str, Any]:
    """Build the portable representation used by docs/tests.

    The server layer converts this representation to the concrete A2A SDK model.
    Keeping this function dependency-light also makes capability discovery easy to test.
    """
    return {
        "name": settings.agent_name,
        "description": settings.agent_description,
        "url": settings.public_url,
        "version": "0.1.0",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "capabilities": {"streaming": True},
        "skills": [
            {
                "id": "local-chat",
                "name": "Local AI",
                "description": "Language-model inference executed by Lemonade on local hardware.",
                "tags": ["local-ai", "lemonade", "amd"],
                "examples": ["Explain why local inference is useful."],
            }
        ],
    }
