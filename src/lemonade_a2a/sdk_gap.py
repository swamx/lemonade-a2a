"""What the installed ``a2a-sdk`` offers that the adapter does not use (``doctor --sdk-gap``).

An SDK upgrade can add possibilities as well as risk. Each probe checks that a capability
exists in the installed SDK and says what the adapter does about it, using the feature
registry as the source of truth.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from dataclasses import dataclass

from . import registry

# (id, title, module, optional attribute with parameter, registry feature, note)
_PROBES: list[tuple[str, str, str, str | None, str | None, str]] = [
    (
        "grpc",
        "gRPC server handler",
        "a2a.server.request_handlers.grpc_handler",
        None,
        "a2a.binding.grpc",
        "",
    ),
    (
        "push",
        "Push notification sender",
        "a2a.server.tasks.push_notification_sender",
        None,
        "a2a.push_notifications",
        "needs the URL policy (safe_urls.py) first",
    ),
    (
        "push_store",
        "Push notification config store",
        "a2a.server.tasks.push_notification_config_store",
        None,
        "a2a.push_notifications",
        "",
    ),
    (
        "v0_3",
        "A2A 0.3 compatibility layer",
        "a2a.compat.v0_3",
        None,
        "a2a.protocol.0_3_compat",
        "disabled in server.py",
    ),
    (
        "db_store",
        "Database task store",
        "a2a.server.tasks.database_task_store",
        None,
        "adapter.task_store.sqlite",
        "used by the sqlite task store",
    ),
    (
        "cluster",
        "Cluster support",
        "a2a.server.cluster",
        None,
        None,
        "not used; single-process adapter",
    ),
    (
        "extended_card",
        "Extended Agent Card hook",
        "a2a.server.request_handlers.default_request_handler_v2",
        "DefaultRequestHandlerV2:extended_card_modifier",
        "a2a.extended_agent_card",
        "",
    ),
    (
        "input_modes",
        "Input-mode validation in the request handler",
        "a2a.server.request_handlers.default_request_handler_v2",
        "DefaultRequestHandlerV2:validate_input_modes",
        None,
        "off; the executor validates parts itself",
    ),
    (
        "queue_manager",
        "Custom queue manager",
        "a2a.server.request_handlers.default_request_handler_v2",
        "DefaultRequestHandlerV2:queue_manager",
        None,
        "accepted but ignored by the v2 handler (why the event-queue bound is not configurable)",
    ),
]


@dataclass(frozen=True)
class Gap:
    id: str
    title: str
    available: bool
    adapter: str  # what the adapter does about it
    feature: str | None
    note: str

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def _available(module: str, attribute: str | None) -> bool:
    try:
        if importlib.util.find_spec(module) is None:
            return False
    except (ImportError, ValueError):
        return False
    if attribute is None:
        return True
    class_name, _, parameter = attribute.partition(":")
    try:
        cls = getattr(importlib.import_module(module), class_name)
        return parameter in inspect.signature(cls.__init__).parameters
    except (ImportError, AttributeError, ValueError, TypeError):
        return False


def probe() -> list[Gap]:
    by_id = {item["id"]: item for item in registry.features()}
    gaps = []
    for gap_id, title, module, attribute, feature_id, note in _PROBES:
        entry = by_id.get(feature_id) if feature_id else None
        if entry is not None:
            adapter = {"supported": "supported", "experimental": "experimental"}.get(
                entry["state"], "not offered"
            )
        else:
            adapter = "not used"
        gaps.append(Gap(gap_id, title, _available(module, attribute), adapter, feature_id, note))
    return gaps


def opportunities(gaps: list[Gap] | None = None) -> list[Gap]:
    """SDK capabilities that exist but that the adapter does not offer."""
    return [
        g
        for g in (gaps if gaps is not None else probe())
        if g.available and g.adapter != "supported"
    ]
