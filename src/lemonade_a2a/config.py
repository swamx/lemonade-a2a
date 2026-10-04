from __future__ import annotations

import os
from dataclasses import dataclass

# Port 9000 is Lemonade Server's WebSocket port on a default install, so the
# adapter must not claim it.
DEFAULT_PORT = 9100


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration, normally loaded with :meth:`from_env`."""

    lemonade_base_url: str = "http://localhost:13305/v1"
    model: str = ""
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    public_url: str = f"http://localhost:{DEFAULT_PORT}"
    agent_name: str = "Lemonade Local Agent"
    agent_description: str = "Private local AI served by Lemonade through A2A."
    request_timeout_seconds: float = 120.0
    max_input_chars: int = 100_000

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError(f"LEMONADE_A2A_PORT must be 1-65535, got {self.port}")
        if self.request_timeout_seconds <= 0:
            raise ValueError("LEMONADE_TIMEOUT_SECONDS must be positive")
        if self.max_input_chars < 1:
            raise ValueError("LEMONADE_A2A_MAX_INPUT_CHARS must be positive")
        if not self.public_url:
            raise ValueError("LEMONADE_A2A_PUBLIC_URL must not be empty")

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        return cls(
            lemonade_base_url=os.getenv("LEMONADE_BASE_URL", defaults.lemonade_base_url),
            model=os.getenv("LEMONADE_MODEL", defaults.model),
            host=os.getenv("LEMONADE_A2A_HOST", defaults.host),
            port=int(os.getenv("LEMONADE_A2A_PORT", str(defaults.port))),
            public_url=os.getenv("LEMONADE_A2A_PUBLIC_URL", defaults.public_url).rstrip("/"),
            agent_name=os.getenv("LEMONADE_A2A_AGENT_NAME", defaults.agent_name),
            agent_description=os.getenv(
                "LEMONADE_A2A_AGENT_DESCRIPTION", defaults.agent_description
            ),
            request_timeout_seconds=float(
                os.getenv("LEMONADE_TIMEOUT_SECONDS", str(defaults.request_timeout_seconds))
            ),
            max_input_chars=int(
                os.getenv("LEMONADE_A2A_MAX_INPUT_CHARS", str(defaults.max_input_chars))
            ),
        )
