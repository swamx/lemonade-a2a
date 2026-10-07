from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from opentelemetry.trace import SpanKind

from .telemetry import NOOP, Telemetry


class StreamFormatError(ValueError):
    """Lemonade sent a stream event that is not the OpenAI chunk shape."""


class BackendStreamError(StreamFormatError):
    """Lemonade reported an error *inside* a successful (HTTP 200) stream.

    Example: a prompt longer than the loaded model's context window arrives as an
    ``{"error": {"code": 400, "type": "exceed_context_size_error", ...}}`` event. Treating
    that event as "no text" would complete the task with an empty answer.
    """

    def __init__(self, error: object) -> None:
        detail = error if isinstance(error, dict) else {}
        self.code = _int(detail.get("code") or detail.get("status_code"))
        self.kind = str(detail.get("type") or "")
        super().__init__("backend reported an error in the stream")


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def describe_stream_error(exc: BackendStreamError) -> str:
    """Client-safe text for an in-stream backend error (details stay in the server log)."""
    if "context" in exc.kind:
        return "The request is longer than the model's context window."
    if 400 <= exc.code < 500:
        return f"Lemonade rejected the request (HTTP {exc.code})."
    return "Lemonade reported an error while generating."


@dataclass(frozen=True, slots=True)
class Delta:
    """One piece of model output: the answer (``content``) or the model's thinking."""

    kind: str  # "content" | "reasoning"
    text: str


def _delta_parts(event: object) -> tuple[str, str]:
    """``(answer text, reasoning text)`` of one OpenAI-style stream event.

    A reasoning model streams its thinking in ``delta.reasoning_content`` before the
    answer in ``delta.content``. An event of the wrong shape is a ``StreamFormatError``.
    """
    if not isinstance(event, dict):
        raise StreamFormatError("unexpected stream event")
    if event.get("error"):
        raise BackendStreamError(event["error"])
    choices = event.get("choices") or []
    if not isinstance(choices, list):
        raise StreamFormatError("unexpected stream choices")
    if not choices:
        return "", ""
    if not isinstance(choices[0], dict) or not isinstance(choices[0].get("delta") or {}, dict):
        raise StreamFormatError("unexpected stream choice")
    delta = choices[0].get("delta") or {}
    content, reasoning = delta.get("content"), delta.get("reasoning_content")
    return (
        content if isinstance(content, str) else "",
        reasoning if isinstance(reasoning, str) else "",
    )


def _delta_text(event: object) -> str:
    """Answer text only (for callers that ignore reasoning)."""
    return _delta_parts(event)[0]


def error_class(exc: BaseException) -> str:
    """Low-cardinality class of a backend failure, for metrics and spans."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "status"
    if isinstance(exc, httpx.HTTPError):
        return "connect"
    if isinstance(exc, BackendStreamError):
        return "stream_error"
    if isinstance(exc, ValueError):
        return "unreadable"
    return "other"


class LemonadeClient:
    """Minimal client for Lemonade's OpenAI-compatible chat surface."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        api_key: str = "",
        telemetry: Telemetry = NOOP,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.telemetry = telemetry
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http: httpx.AsyncClient | None = None
        parts = urlsplit(self.base_url)
        self._server = (parts.hostname or "", parts.port)

    def _client(self) -> httpx.AsyncClient:
        # One pooled client: building an AsyncClient per request loads a fresh
        # SSL context, which costs hundreds of milliseconds of TTFT on Windows.
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout, headers=self._headers)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _payload(self, messages: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {"messages": messages, "stream": stream}
        if self.model:
            payload["model"] = self.model
        return payload

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        response = await self._client().post(
            f"{self.base_url}/chat/completions",
            json=self._payload(messages, stream=False),
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    async def stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Yield the answer text deltas from Lemonade's OpenAI-compatible SSE stream."""
        async for delta in self.stream_events(messages):
            if delta.kind == "content":
                yield delta.text

    async def stream_events(self, messages: list[dict[str, Any]]) -> AsyncIterator[Delta]:
        """Yield answer and reasoning deltas, in order."""
        telemetry = self.telemetry
        headers: dict[str, str] = {}
        started = time.perf_counter()
        failure: str | None = None
        span = telemetry.start_span(
            "lemonade.chat.stream",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": self.model or None,
                "server.address": self._server[0],
                "server.port": self._server[1],
            },
        )
        telemetry.inject(headers, span)  # forward the W3C trace context to Lemonade
        try:
            try:
                async with self._client().stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=self._payload(messages, stream=True),
                    headers=headers or None,
                ) as response:
                    span.set_attribute("http.response.status_code", response.status_code)
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        event = json.loads(raw)
                        self._record_usage(event)
                        content, reasoning = _delta_parts(event)
                        if reasoning:
                            yield Delta("reasoning", reasoning)
                        if content:
                            yield Delta("content", content)
                telemetry.backend_ok()
            except BaseException as exc:
                # Cancellation, deadlines and a consumer that stops reading are not backend failures.
                if not isinstance(exc, GeneratorExit | asyncio.CancelledError):
                    failure = error_class(exc)
                    telemetry.backend_error(failure)
                    span.set_attribute("error.type", failure)
                    telemetry.fail(span, f"lemonade {failure} error")
                raise
            finally:
                telemetry.backend_call(time.perf_counter() - started, self.model, failure)
        finally:
            span.end()

    def _record_usage(self, event: object) -> None:
        usage = event.get("usage") if isinstance(event, dict) else None
        if isinstance(usage, dict):
            for key, kind in (("prompt_tokens", "input"), ("completion_tokens", "output")):
                value = usage.get(key)
                if isinstance(value, int) and value >= 0:
                    self.telemetry.token_usage(self.model, kind, value)
