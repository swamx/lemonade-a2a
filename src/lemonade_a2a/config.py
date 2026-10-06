from __future__ import annotations

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

# Port 9000 is Lemonade Server's WebSocket port on a default install, so the
# adapter must not claim it.
DEFAULT_PORT = 9100

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PROFILES = ("local", "lan", "external")
CAPTURE_MODES = ("none", "metadata", "content")
LOG_FORMATS = ("text", "json")
COMPAT_MODES = ("off", "warn", "strict")
SECRET_FIELDS = frozenset({"api_key", "api_keys", "lemonade_api_key"})


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
    streaming: bool = True  # a core feature; switch off with LEMONADE_A2A_FEATURES=-a2a.streaming
    cancel_on_disconnect: bool = False
    reasoning: str = "drop"  # drop | artifact: what to do with a reasoning model's thinking
    rate_limit_per_minute: int = 0  # per identity; 0 turns rate limiting off

    # Security. Secrets are excluded from repr so they never reach logs.
    api_key: str = field(default="", repr=False)
    api_keys: str = field(default="", repr=False)
    lemonade_api_key: str = field(default="", repr=False)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    ssl_ca_certs: str = ""
    ssl_require_client_cert: bool = False

    # Observability (docs/observability.md). Off by default; OTEL_* variables configure
    # exporters, sampling and resource attributes.
    otel_enabled: bool = False
    otel_capture: str = "none"  # none | metadata | content
    otel_content_max_chars: int = 1024
    otel_redact: str = ""  # comma-separated attribute keys or patterns to drop
    otel_prometheus: bool = False  # serve /metrics (authenticated like other routes)
    otel_buffer: int = 2048  # queued spans/logs before dropping
    otel_allow_plaintext: bool = False  # allow a non-TLS remote OTLP endpoint under `external`
    log_format: str = "text"  # text | json (json carries trace_id/span_id)

    # Control and flexibility (docs/specification.md)
    compat: str = "warn"  # off | warn | strict: what to do when startup compatibility checks fail
    features: str = ""  # "+adapter.cancel_on_disconnect,-a2a.streaming": registry-id toggles
    expose_capabilities: bool = False  # serve the authenticated capabilities endpoint
    backend: str = "lemonade"  # extension: lemonade_a2a.backends entry point
    task_store: str = "memory"  # memory | sqlite | a lemonade_a2a.task_stores entry point
    task_db: str = "lemonade-a2a-tasks.sqlite"  # file used by the sqlite task store
    authenticator: str = ""  # extension: lemonade_a2a.authenticators entry point
    telemetry_plugin: str = ""  # extension: lemonade_a2a.telemetry entry point

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
        self._apply_feature_flags()
        if self.compat not in COMPAT_MODES:
            raise ValueError(f"LEMONADE_A2A_COMPAT must be one of {', '.join(COMPAT_MODES)}")
        if not self.task_store or not self.backend:
            raise ValueError("LEMONADE_A2A_TASK_STORE and LEMONADE_A2A_BACKEND must not be empty")
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
            "LEMONADE_A2A_OTEL_BUFFER": self.otel_buffer,
            "LEMONADE_A2A_OTEL_CONTENT_MAX_CHARS": self.otel_content_max_chars,
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
        if self.otel_capture not in CAPTURE_MODES:
            raise ValueError(f"LEMONADE_A2A_OTEL_CAPTURE must be one of {', '.join(CAPTURE_MODES)}")
        if self.reasoning not in ("drop", "artifact"):
            raise ValueError("LEMONADE_A2A_REASONING must be drop or artifact")
        if self.log_format not in LOG_FORMATS:
            raise ValueError(f"LEMONADE_A2A_LOG_FORMAT must be one of {', '.join(LOG_FORMATS)}")
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
        if not (self.credentials or self.ssl_require_client_cert or self.authenticator):
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
            self._check_external_telemetry()

    def _check_external_telemetry(self) -> None:
        if not self.otel_enabled:
            return
        if self.otel_capture == "content":
            raise ValueError(
                "profile 'external' refuses LEMONADE_A2A_OTEL_CAPTURE=content "
                "(prompts and responses would leave the host)"
            )
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        host = endpoint.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].strip("[]")
        if (
            endpoint.startswith("http://")
            and host not in LOOPBACK_HOSTS
            and not self.otel_allow_plaintext
        ):
            raise ValueError(
                "profile 'external' needs a TLS OTLP endpoint (https://...); "
                "set LEMONADE_A2A_OTEL_ALLOW_PLAINTEXT=1 to accept plaintext"
            )

    def _apply_feature_flags(self) -> None:
        """``+id`` / ``-id`` in ``features`` set the setting the registry ties to that id."""
        if not self.features.strip():
            return
        from .registry import toggles

        known = toggles()
        for item in filter(None, (part.strip() for part in self.features.split(","))):
            sign, feature_id = item[0], item[1:]
            if sign not in "+-" or feature_id not in known:
                raise ValueError(
                    f"LEMONADE_A2A_FEATURES entry {item!r} is not '+id' or '-id' of a toggleable "
                    "feature (see `lemonade-a2a capabilities`)"
                )
            toggle = known[feature_id]
            object.__setattr__(self, toggle["setting"], toggle["on" if sign == "+" else "off"])

    # ----- loading ------------------------------------------------------------------

    @classmethod
    def env_values(cls, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Settings named by environment variables, typed; absent variables are omitted."""
        env = os.environ if env is None else env
        return {
            field_name: convert(env[name])
            for name, (field_name, convert) in _ENV.items()
            if name in env
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        return cls(**cls.env_values(env))

    @classmethod
    def load(
        cls,
        config_file: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> tuple[Settings, dict[str, str]]:
        """Layered configuration: defaults, then the config file, the environment, then
        ``overrides`` (command-line flags). Returns the settings and where each non-default
        value came from (``file``, ``env`` or ``cli``)."""
        env = os.environ if env is None else env
        path = config_file or env.get("LEMONADE_A2A_CONFIG")
        file_values = read_config_file(path) if path else {}
        env_values = cls.env_values(env)
        cli_values = dict(overrides or {})
        unknown = set(cli_values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        merged = {**file_values, **env_values, **cli_values}
        sources = {
            **dict.fromkeys(file_values, "file"),
            **dict.fromkeys(env_values, "env"),
            **dict.fromkeys(cli_values, "cli"),
        }
        return cls(**merged), sources

    # ----- introspection (config show / schema) -------------------------------------

    def as_dict(self, *, redact: bool = True) -> dict[str, Any]:
        values = {f.name: getattr(self, f.name) for f in fields(self)}
        if redact:
            for name in SECRET_FIELDS:
                values[name] = "<set>" if values[name] else "<unset>"
        return values

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """JSON Schema of every setting (type, default, environment variable, allowed values)."""
        kinds = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
        env_names = {field_name: name for name, (field_name, _) in _ENV.items()}
        choices = {
            "profile": PROFILES,
            "otel_capture": CAPTURE_MODES,
            "log_format": LOG_FORMATS,
            "compat": COMPAT_MODES,
            "reasoning": ("drop", "artifact"),
        }
        properties: dict[str, Any] = {}
        for f in fields(cls):
            entry: dict[str, Any] = {"type": kinds.get(str(f.type), "string")}
            if f.default is not MISSING:
                secret = f.name in SECRET_FIELDS and f.default
                entry["default"] = "<secret>" if secret else f.default
            if f.name in env_names:
                entry["x-env"] = env_names[f.name]
            if f.name in choices:
                entry["enum"] = list(choices[f.name])
            if f.name in SECRET_FIELDS:
                entry["writeOnly"] = True
            properties[f.name] = entry
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "lemonade-a2a settings",
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
        }


def read_config_file(path: str | Path) -> dict[str, Any]:
    """Settings from a TOML file: a ``[lemonade_a2a]`` table whose keys are setting names."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"config file is not valid TOML: {path}: {exc}") from exc
    table = data.get("lemonade_a2a", data)
    known = {f.name for f in fields(Settings)}
    unknown = set(table) - known
    if unknown:
        raise ValueError(f"unknown setting(s) in {path}: {', '.join(sorted(unknown))}")
    return dict(table)


def _text(value: str) -> str:
    return value


def _lower(value: str) -> str:
    return value.strip().lower()


def _url(value: str) -> str:
    return value.rstrip("/")


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


_ENV: dict[str, tuple[str, Callable[[str], Any]]] = {
    "LEMONADE_BASE_URL": ("lemonade_base_url", _text),
    "LEMONADE_MODEL": ("model", _text),
    "LEMONADE_A2A_HOST": ("host", _text),
    "LEMONADE_A2A_PORT": ("port", int),
    "LEMONADE_A2A_PUBLIC_URL": ("public_url", _url),
    "LEMONADE_A2A_AGENT_NAME": ("agent_name", _text),
    "LEMONADE_A2A_AGENT_DESCRIPTION": ("agent_description", _text),
    "LEMONADE_A2A_PROFILE": ("profile", _lower),
    "LEMONADE_TIMEOUT_SECONDS": ("request_timeout_seconds", float),
    "LEMONADE_A2A_MAX_INPUT_CHARS": ("max_input_chars", int),
    "LEMONADE_A2A_MAX_INPUT_PARTS": ("max_input_parts", int),
    "LEMONADE_A2A_MAX_REQUEST_BYTES": ("max_request_bytes", int),
    "LEMONADE_A2A_MAX_TASK_SECONDS": ("max_task_seconds", float),
    "LEMONADE_A2A_MAX_CONCURRENT_TASKS": ("max_concurrent_tasks", int),
    "LEMONADE_A2A_MAX_STORED_TASKS": ("max_stored_tasks", int),
    "LEMONADE_A2A_CANCEL_ON_DISCONNECT": ("cancel_on_disconnect", _flag),
    "LEMONADE_A2A_REASONING": ("reasoning", _lower),
    "LEMONADE_A2A_RATE_LIMIT_PER_MINUTE": ("rate_limit_per_minute", int),
    "LEMONADE_A2A_API_KEY": ("api_key", _text),
    "LEMONADE_A2A_API_KEYS": ("api_keys", _text),
    "LEMONADE_API_KEY": ("lemonade_api_key", _text),
    "LEMONADE_A2A_SSL_CERTFILE": ("ssl_certfile", _text),
    "LEMONADE_A2A_SSL_KEYFILE": ("ssl_keyfile", _text),
    "LEMONADE_A2A_SSL_CA_CERTS": ("ssl_ca_certs", _text),
    "LEMONADE_A2A_SSL_REQUIRE_CLIENT_CERT": ("ssl_require_client_cert", _flag),
    "LEMONADE_A2A_OTEL": ("otel_enabled", _flag),
    "LEMONADE_A2A_OTEL_CAPTURE": ("otel_capture", _lower),
    "LEMONADE_A2A_OTEL_CONTENT_MAX_CHARS": ("otel_content_max_chars", int),
    "LEMONADE_A2A_OTEL_REDACT": ("otel_redact", _text),
    "LEMONADE_A2A_OTEL_PROMETHEUS": ("otel_prometheus", _flag),
    "LEMONADE_A2A_OTEL_BUFFER": ("otel_buffer", int),
    "LEMONADE_A2A_OTEL_ALLOW_PLAINTEXT": ("otel_allow_plaintext", _flag),
    "LEMONADE_A2A_LOG_FORMAT": ("log_format", _lower),
    "LEMONADE_A2A_COMPAT": ("compat", _lower),
    "LEMONADE_A2A_FEATURES": ("features", _text),
    "LEMONADE_A2A_EXPOSE_CAPABILITIES": ("expose_capabilities", _flag),
    "LEMONADE_A2A_BACKEND": ("backend", _lower),
    "LEMONADE_A2A_TASK_STORE": ("task_store", _lower),
    "LEMONADE_A2A_TASK_DB": ("task_db", _text),
    "LEMONADE_A2A_AUTHENTICATOR": ("authenticator", _lower),
    "LEMONADE_A2A_TELEMETRY_PLUGIN": ("telemetry_plugin", _lower),
}
