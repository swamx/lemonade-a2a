from __future__ import annotations

import os
from dataclasses import dataclass, field

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

    # Resource bounds
    request_timeout_seconds: float = 120.0
    max_input_chars: int = 100_000
    max_input_parts: int = 32
    max_task_seconds: float = 600.0
    max_concurrent_tasks: int = 8
    max_stored_tasks: int = 1000

    # Security. Secrets are excluded from repr so they never reach logs.
    api_key: str = field(default="", repr=False)
    lemonade_api_key: str = field(default="", repr=False)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError(f"LEMONADE_A2A_PORT must be 1-65535, got {self.port}")
        if not self.public_url:
            raise ValueError("LEMONADE_A2A_PUBLIC_URL must not be empty")
        positive = {
            "LEMONADE_TIMEOUT_SECONDS": self.request_timeout_seconds,
            "LEMONADE_A2A_MAX_INPUT_CHARS": self.max_input_chars,
            "LEMONADE_A2A_MAX_INPUT_PARTS": self.max_input_parts,
            "LEMONADE_A2A_MAX_TASK_SECONDS": self.max_task_seconds,
            "LEMONADE_A2A_MAX_CONCURRENT_TASKS": self.max_concurrent_tasks,
            "LEMONADE_A2A_MAX_STORED_TASKS": self.max_stored_tasks,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if bool(self.ssl_certfile) != bool(self.ssl_keyfile):
            raise ValueError("LEMONADE_A2A_SSL_CERTFILE and LEMONADE_A2A_SSL_KEYFILE go together")

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        env = os.environ.get

        def number(name: str, default, kind=int):
            return kind(env(name, str(default)))

        return cls(
            lemonade_base_url=env("LEMONADE_BASE_URL", defaults.lemonade_base_url),
            model=env("LEMONADE_MODEL", defaults.model),
            host=env("LEMONADE_A2A_HOST", defaults.host),
            port=number("LEMONADE_A2A_PORT", defaults.port),
            public_url=env("LEMONADE_A2A_PUBLIC_URL", defaults.public_url).rstrip("/"),
            agent_name=env("LEMONADE_A2A_AGENT_NAME", defaults.agent_name),
            agent_description=env("LEMONADE_A2A_AGENT_DESCRIPTION", defaults.agent_description),
            request_timeout_seconds=number(
                "LEMONADE_TIMEOUT_SECONDS", defaults.request_timeout_seconds, float
            ),
            max_input_chars=number("LEMONADE_A2A_MAX_INPUT_CHARS", defaults.max_input_chars),
            max_input_parts=number("LEMONADE_A2A_MAX_INPUT_PARTS", defaults.max_input_parts),
            max_task_seconds=number(
                "LEMONADE_A2A_MAX_TASK_SECONDS", defaults.max_task_seconds, float
            ),
            max_concurrent_tasks=number(
                "LEMONADE_A2A_MAX_CONCURRENT_TASKS", defaults.max_concurrent_tasks
            ),
            max_stored_tasks=number("LEMONADE_A2A_MAX_STORED_TASKS", defaults.max_stored_tasks),
            api_key=env("LEMONADE_A2A_API_KEY", defaults.api_key),
            lemonade_api_key=env("LEMONADE_API_KEY", defaults.lemonade_api_key),
            ssl_certfile=env("LEMONADE_A2A_SSL_CERTFILE", defaults.ssl_certfile),
            ssl_keyfile=env("LEMONADE_A2A_SSL_KEYFILE", defaults.ssl_keyfile),
        )
