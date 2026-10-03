from __future__ import annotations

import logging

from .agent_card import build_agent_card
from .config import Settings

LOG = logging.getLogger("lemonade_a2a")


def create_app():
    """Create the A2A application using the installed official SDK.

    A2A SDK APIs have evolved quickly. The concrete binding is intentionally
    kept in this one module so protocol-version changes do not leak into the
    Lemonade client/execution core.
    """
    settings = Settings.from_env()
    card = build_agent_card(settings)

    try:
        from a2a.server.apps import A2AStarletteApplication
        from a2a.types import AgentCapabilities, AgentCard, AgentSkill
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install project dependencies with: pip install -e .") from exc

    skills = [AgentSkill(**skill) for skill in card["skills"]]
    agent_card = AgentCard(
        name=card["name"],
        description=card["description"],
        url=card["url"],
        version=card["version"],
        default_input_modes=card["defaultInputModes"],
        default_output_modes=card["defaultOutputModes"],
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
    )

    # The next implementation milestone wires the official SDK request handler
    # and AgentExecutor to LemonadeAgent. Keeping startup explicit prevents this
    # pre-alpha scaffold from pretending unsupported task semantics are complete.
    try:
        return A2AStarletteApplication(agent_card=agent_card).build()
    except TypeError as exc:
        raise RuntimeError(
            "The installed a2a-sdk server API differs from this pre-alpha binding. "
            "See docs/roadmap.md for the version-pinning milestone."
        ) from exc


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO)
    LOG.info("Starting Lemonade A2A on %s:%s", settings.host, settings.port)
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
