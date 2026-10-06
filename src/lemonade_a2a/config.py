from __future__ import annotations

import os
from dataclasses import dataclass, field

# Port 9000 is Lemonade Server's WebSocket port on a default install, so the
# adapter must not claim it.
DEFAULT_PORT = 9100

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PROFILES = ("local", "lan", "external")


def parse_api_keys(spec: str) -> dict[str, str]:
    """Parse ``name:key,name2:key2`` into ``{name: key}`` (the name is the task owner)."""
    keys: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in spec.split(","))):
        name, separator, key = item.partition(":")
        if not separator or not name.strip() or not key:
            raise ValueError("LEMONADE_A2A_API_KEYS entries must look like name:key")
        name = name.strip()
        if name in keys:
            raise ValueError(f"LEMONADE_A2A_API_KEYS names a user twice: {name}")
        keys[name] = key
    return keys


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration, normally loaded with :meth:`from_env`.

    ``profile`` states how the adapter is exposed and the combination is checked at
    construction (see docs/security.md): ``local`` listens on loopback only; ``lan``
    needs authentication; ``external`` also needs TLS and rate limiting.
    """

    lemonade_base_url: str = "http://localhost:13305/v1"
    model: str = ""
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    public_url: str = f"http://localhost:{DEFAULT_PORT}"
    agent_name: str = "Lemonade Local Agent"
    agent_description: str = "Private local AI served by Lemonade through A2A."
    profile: str = "local"

    # Resource bounds
    request_timeout_seconds: float = 120.0
    max_input_chars: int = 100_000
    max_input_parts: int = 32
    max_request_bytes: int = 1_048_576
    max_task_seconds: float = 600.0
    max_concurrent_tasks: int = 8
    max_stored_tasks: int = 1000
    cancel_on_disconnect: bool = False
    rate_limit_per_minute: int = 0  # per identity; 0 turns rate limiting off

    # Security. Secrets are excluded from repr so they never reach logs.
    api_key: str = field(default="", repr=False)
    api_keys: str = field(default="", repr=False)
    lemonade_api_key: str = field(default="", repr=False)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    ssl_ca_certs: str = ""
    ssl_require_client_cert: bool = False

    @property
    def credentials(self) -> dict[str, str]:
        """Accepted API keys by identity. ``api_key`` is the single-user shortcut."""
        keys = parse_api_keys(self.api_keys)
        if self.api_key:
            if "default" in keys:
                raise ValueError("LEMONADE_A2A_API_KEYS must not name a user 'default'")
            keys["default"] = self.api_key
        return keys

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError(f"LEMONADE_A2A_PORT must be 1-65535, got {self.port}")
        if not self.public_url:
            raise ValueError("LEMONADE_A2A_PUBLIC_URL must not be empty")
        positive = {
            "LEMONADE_TIMEOUT_SECONDS": self.request_timeout_seconds,
            "LEMONADE_A2A_MAX_INPUT_CHARS": self.max_input_chars,
            "LEMONADE_A2A_MAX_INPUT_PARTS": self.max_input_parts,
            "LEMONADE_A2A_MAX_REQUEST_BYTES": self.max_request_bytes,
            "LEMONADE_A2A_MAX_TASK_SECONDS": self.max_task_seconds,
            "LEMONADE_A2A_MAX_CONCURRENT_TASKS": self.max_concurrent_tasks,
            "LEMONADE_A2A_MAX_STORED_TASKS": self.max_stored_tasks,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.rate_limit_per_minute < 0:
            raise ValueError("LEMONADE_A2A_RATE_LIMIT_PER_MINUTE must not be negative")
        if bool(self.ssl_certfile) != bool(self.ssl_keyfile):
            raise ValueError("LEMONADE_A2A_SSL_CERTFILE and LEMONADE_A2A_SSL_KEYFILE go together")
        if self.ssl_require_client_cert and not (self.ssl_certfile and self.ssl_ca_certs):
            raise ValueError(
                "LEMONADE_A2A_SSL_REQUIRE_CLIENT_CERT needs the server certificate and "
                "LEMONADE_A2A_SSL_CA_CERTS (the CA that signs client certificates)"
            )
        self._check_profile()

    def _check_profile(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"LEMONADE_A2A_PROFILE must be one of {', '.join(PROFILES)}")
        if self.profile == "local":
            if self.host not in LOOPBACK_HOSTS:
                raise ValueError(
                    f"profile 'local' listens on loopback only, not {self.host!r}; "
                    "set LEMONADE_A2A_PROFILE=lan (or external) to expose the adapter"
                )
            return
        if not (self.credentials or self.ssl_require_client_cert):
            raise ValueError(
                f"profile {self.profile!r} needs authentication: set LEMONADE_A2A_API_KEY, "
                "LEMONADE_A2A_API_KEYS or client certificates"
            )
        if self.profile == "external":
            if not self.ssl_certfile:
                raise ValueError(
                    "profile 'external' needs TLS: set LEMONADE_A2A_SSL_CERTFILE/KEYFILE"
                )
            if not self.rate_limit_per_minute:
                raise ValueError(
                    "profile 'external' needs rate limiting: set LEMONADE_A2A_RATE_LIMIT_PER_MINUTE"
                )

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        env = os.environ.get

        def number(name: str, default, kind=int):
            return kind(env(name, str(default)))

        def flag(name: str, default: bool) -> bool:
            return env(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")

        return cls(
            lemonade_base_url=env("LEMONADE_BASE_URL", defaults.lemonade_base_url),
            model=env("LEMONADE_MODEL", defaults.model),
            host=env("LEMONADE_A2A_HOST", defaults.host),
            port=number("LEMONADE_A2A_PORT", defaults.port),
            public_url=env("LEMONADE_A2A_PUBLIC_URL", defaults.public_url).rstrip("/"),
            agent_name=env("LEMONADE_A2A_AGENT_NAME", defaults.agent_name),
            agent_description=env("LEMONADE_A2A_AGENT_DESCRIPTION", defaults.agent_description),
            profile=env("LEMONADE_A2A_PROFILE", defaults.profile).strip().lower(),
            request_timeout_seconds=number(
                "LEMONADE_TIMEOUT_SECONDS", defaults.request_timeout_seconds, float
            ),
            max_input_chars=number("LEMONADE_A2A_MAX_INPUT_CHARS", defaults.max_input_chars),
            max_input_parts=number("LEMONADE_A2A_MAX_INPUT_PARTS", defaults.max_input_parts),
            max_request_bytes=number("LEMONADE_A2A_MAX_REQUEST_BYTES", defaults.max_request_bytes),
            max_task_seconds=number(
                "LEMONADE_A2A_MAX_TASK_SECONDS", defaults.max_task_seconds, float
            ),
            max_concurrent_tasks=number(
                "LEMONADE_A2A_MAX_CONCURRENT_TASKS", defaults.max_concurrent_tasks
            ),
            max_stored_tasks=number("LEMONADE_A2A_MAX_STORED_TASKS", defaults.max_stored_tasks),
            cancel_on_disconnect=flag(
                "LEMONADE_A2A_CANCEL_ON_DISCONNECT", defaults.cancel_on_disconnect
            ),
            rate_limit_per_minute=number(
                "LEMONADE_A2A_RATE_LIMIT_PER_MINUTE", defaults.rate_limit_per_minute
            ),
            api_key=env("LEMONADE_A2A_API_KEY", defaults.api_key),
            api_keys=env("LEMONADE_A2A_API_KEYS", defaults.api_keys),
            lemonade_api_key=env("LEMONADE_API_KEY", defaults.lemonade_api_key),
            ssl_certfile=env("LEMONADE_A2A_SSL_CERTFILE", defaults.ssl_certfile),
            ssl_keyfile=env("LEMONADE_A2A_SSL_KEYFILE", defaults.ssl_keyfile),
            ssl_ca_certs=env("LEMONADE_A2A_SSL_CA_CERTS", defaults.ssl_ca_certs),
            ssl_require_client_cert=flag(
                "LEMONADE_A2A_SSL_REQUIRE_CLIENT_CERT", defaults.ssl_require_client_cert
            ),
        )
