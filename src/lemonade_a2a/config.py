from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class Settings:
    lemonade_base_url: str = "http://localhost:8000/v1"
    model: str = ""
    host: str = "127.0.0.1"
    port: int = 9000
    public_url: str = "http://localhost:9000"
    agent_name: str = "Lemonade Local Agent"
    agent_description: str = "Private local AI served by AMD Lemonade through A2A."

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            lemonade_base_url=os.getenv("LEMONADE_BASE_URL", cls.lemonade_base_url),
            model=os.getenv("LEMONADE_MODEL", ""),
            host=os.getenv("LEMONADE_A2A_HOST", cls.host),
            port=int(os.getenv("LEMONADE_A2A_PORT", str(cls.port))),
            public_url=os.getenv("LEMONADE_A2A_PUBLIC_URL", cls.public_url).rstrip("/"),
            agent_name=os.getenv("LEMONADE_A2A_AGENT_NAME", cls.agent_name),
            agent_description=os.getenv(
                "LEMONADE_A2A_AGENT_DESCRIPTION", cls.agent_description
            ),
        )
