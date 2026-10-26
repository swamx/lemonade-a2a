"""Official A2A SDK client facade.

For modern Agent Cards and tasks, reuse the upstream SDK instead of
maintaining an independent JSON-RPC, REST or streaming implementation.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from a2a.client import ClientConfig, ClientFactory
from a2a.types import SendMessageRequest, StreamResponse


class A2AClient:
    """Connect to an agent advertised at a trusted base URL.

    Callers construct SDK-native protobuf `SendMessageRequest` objects;
    no merchant-specific action schema is imposed on the core API.
    """

    def __init__(self, base_url: str, *, streaming: bool = False) -> None:
        self.base_url = base_url
        self.streaming = streaming

    async def send_message(
        self, request: SendMessageRequest
    ) -> AsyncIterator[StreamResponse]:
        client = await ClientFactory(
            ClientConfig(streaming=self.streaming)
        ).create_from_url(self.base_url)
        async with client:
            async for response in client.send_message(request):
                yield response
