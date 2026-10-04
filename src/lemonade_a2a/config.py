from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    lemonade_base_url: str = "http://localhost:13305/v1"
    model: str = ""
    host: str = "127.0.0.1"
    port: int = 9000
    public_url: str = "http://localhost:9000"
    agent_name: str = "Lemonade Local Agent"
    agent_description: str = "Private local AI served by Lemonade through A2A."

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        return cls(
            lemonade_base_url=os.getenv("LEMONADE_BASE_URL", defaults.lemonade_base_url),
            model=os.getenv("LEMONADE_MODEL", ""),
            host=os.getenv("LEMONADE_A2A_HOST", defaults.host),
            port=int(os.getenv("LEMONADE_A2A_PORT", str(defaults.port))),
            public_url=os.getenv("LEMONADE_A2A_PUBLIC_URL", defaults.public_url).rstrip("/"),
            agent_name=os.getenv("LEMONADE_A2A_AGENT_NAME", defaults.agent_name),
            agent_description=os.getenv(
                "LEMONADE_A2A_AGENT_DESCRIPTION", defaults.agent_description
            ),
        )
