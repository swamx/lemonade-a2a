"""Provider-neutral application view; upstream SDK objects remain authoritative."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentResult:
    agent_id: str
    task_id: str | None
    status: str
    artifacts: list[Any]
    correlation_id: str
