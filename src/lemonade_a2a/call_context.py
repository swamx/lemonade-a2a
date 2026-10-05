"""Builds the A2A ``ServerCallContext`` for each request."""

from __future__ import annotations

from a2a.server.context import ServerCallContext
from a2a.server.routes.common import DefaultServerCallContextBuilder
from starlette.requests import Request

from .middleware import DISCONNECT_KEY


class LemonadeCallContextBuilder(DefaultServerCallContextBuilder):
    """Default context plus the client-disconnect event (``state["disconnected"]``).

    The authenticated user comes from ``scope["user"]``, which the API-key middleware
    sets; the SDK keys task ownership on it.
    """

    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        context.state["disconnected"] = request.scope.get(DISCONNECT_KEY)
        return context
