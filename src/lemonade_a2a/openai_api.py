"""An OpenAI-compatible chat surface for the agent, so chat apps can talk to it.

Lemonade's own web app (and any OpenAI-compatible client) can add an agent as a model:
Lemonade registers it as a "cloud" provider (``lemonade-a2a lemonade-ui add``, or the
web app's settings), lists it in the model picker, and proxies chat requests to it. The
agent then answers exactly as it does over A2A: the same executor, so delegation to
marketplace agents, the per-task deadline, the concurrency cap, authentication and rate
limits all apply.

``GET /v1/models`` lists one model, the agent (its id is a slug of the agent name).
``POST /v1/chat/completions`` accepts the usual ``messages`` (text, and ``image_url``
data URLs when images are enabled) and ``stream``; sampling parameters are ignored
because the agent, not the caller, decides how the model is used. Thinking and the
"consulting agent X" notes are streamed as ``reasoning_content``, which chat apps show
apart from the answer.

The OpenAI API is stateless: each request carries the whole conversation. That history
is what the model sees. To keep a marketplace agent's own conversation (and a question
it asked) across requests, each conversation gets an internal context id, found again
from a digest of the messages that came before (caller, earlier messages and the answer
given), so a reply continues the right remote conversation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .executor import (
    IMAGE_MEDIA_TYPES,
    BusyError,
    NoAnswerError,
    Turn,
    describe_backend_error,
)
from .lemonade_client import BackendStreamError, describe_stream_error

LOG = logging.getLogger("lemonade_a2a.openai_api")

MAX_MESSAGES = 200
ROLES = ("system", "user", "assistant")
_DATA_URL = re.compile(r"^data:([a-z0-9.+-]+/[a-z0-9.+-]+);base64,[A-Za-z0-9+/=]+$", re.IGNORECASE)


def model_id(agent_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", agent_name.lower()).strip("-")[:64] or "agent"


class RequestError(ValueError):
    """A request this surface will not run (answered as HTTP 400)."""


def _error(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind, "code": status}}, status)


def _text_and_images(content: Any, *, images_allowed: bool) -> tuple[str, list[str]]:
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        raise RequestError("message content must be a string or a list of parts")
    texts: list[str] = []
    images: list[str] = []
    for part in content:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url") if isinstance(part, dict) else None
            match = _DATA_URL.match(url or "")
            if not images_allowed:
                raise RequestError("this agent does not accept images (LEMONADE_A2A_FILE_IMAGES)")
            if not match or match.group(1).lower() not in IMAGE_MEDIA_TYPES:
                # Remote image URLs would be a server-side fetch; only inline data is taken.
                raise RequestError("images must be inline data: URLs of a supported type")
            images.append(url)
        else:
            raise RequestError(f"unsupported content part: {kind!r}")
    return "\n".join(texts), images


def _history_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                pieces.append(str(part.get("text", "")))
            elif isinstance(part, dict) and part.get("type") == "image_url":
                pieces.append("[image]")
        return "\n".join(pieces)
    return ""


class OpenAICompat:
    """The routes, plus the small state that maps stateless requests to conversations."""

    def __init__(self, settings, executor) -> None:
        self.settings = settings
        self.executor = executor
        self.model = model_id(settings.agent_name)
        self.created = int(time.time())
        self._contexts: OrderedDict[str, str] = OrderedDict()
        self._max_contexts = settings.max_stored_tasks

    # -- conversations --------------------------------------------------------------------

    @staticmethod
    def _digest(owner: str, messages: list[dict[str, Any]]) -> str:
        payload = json.dumps([owner, messages], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _context_for(self, owner: str, earlier: list[dict[str, Any]]) -> str:
        if earlier:
            found = self._contexts.get(self._digest(owner, earlier))
            if found:
                return found
        return uuid.uuid4().hex

    def _remember(self, owner: str, messages: list[dict[str, Any]], context_id: str) -> None:
        self._contexts[self._digest(owner, messages)] = context_id
        self._contexts.move_to_end(self._digest(owner, messages))
        while len(self._contexts) > self._max_contexts:
            self._contexts.popitem(last=False)

    # -- requests -------------------------------------------------------------------------

    def parse(self, body: Any, owner: str) -> tuple[Turn, list[dict[str, Any]], bool]:
        if not isinstance(body, dict):
            raise RequestError("request body must be a JSON object")
        requested = body.get("model")
        # Lemonade sends its own prefixed name ("<provider>.<id>"); accept that form too.
        known = not requested or (
            isinstance(requested, str) and requested.split(".")[-1] == self.model
        )
        if not known:
            raise RequestError(f"unknown model {requested!r}; this agent is {self.model!r}")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
            raise RequestError(f"messages must be a list of 1 to {MAX_MESSAGES} messages")
        images_allowed = bool(getattr(self.executor, "accept_images", False))
        clean: list[dict[str, Any]] = []
        total = 0
        last_images: list[str] = []
        for index, message in enumerate(messages):
            role = message.get("role") if isinstance(message, dict) else None
            role = "system" if role == "developer" else role
            if role == "tool":
                continue  # the agent does not run the caller's tools; their output is not used
            if role not in ROLES:
                raise RequestError(f"messages[{index}] needs a role of {', '.join(ROLES)}")
            is_last = index == len(messages) - 1
            content = message.get("content") or ""
            if is_last:
                text, last_images = _text_and_images(content, images_allowed=images_allowed)
            else:  # earlier turns: keep the text, mark images (they are not sent again)
                text = _history_text(content)
            total += len(text)
            clean.append({"role": role, "content": text})
        if (
            not clean
            or clean[-1]["role"] != "user"
            or not (clean[-1]["content"].strip() or last_images)
        ):
            raise RequestError("the last message must be a non-empty user message")
        if total > self.executor.max_input_chars:
            raise RequestError(
                f"conversation exceeds the {self.executor.max_input_chars} character limit"
            )
        earlier = clean[:-1]
        turn = Turn(
            text=clean[-1]["content"],
            images=last_images,
            owner=owner,
            context_id=self._context_for(owner, earlier),
            history=earlier,
        )
        return turn, clean, bool(body.get("stream"))

    def _chunk(self, completion_id: str, delta: dict[str, Any], finish: str | None = None) -> str:
        event = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    @staticmethod
    def _failure_text(exc: BaseException) -> tuple[int, str]:
        if isinstance(exc, BusyError):
            return 429, str(exc)
        if isinstance(exc, TimeoutError):
            return 504, "The agent exceeded its time limit."
        if isinstance(exc, httpx.HTTPError):
            return 502, describe_backend_error(exc)
        if isinstance(exc, BackendStreamError):
            return 502, describe_stream_error(exc)
        if isinstance(exc, NoAnswerError):
            return 502, "The model returned reasoning but no answer."
        return 502, "The agent could not answer."

    @staticmethod
    def _closing_note(turn: Turn) -> str:
        if not turn.artifacts:
            return ""
        names = [
            part.filename or part.media_type or "file"
            for _, parts in turn.artifacts
            for part in parts
        ]
        return (
            "\n\n(Files returned by consulted agents, not shown in this chat: "
            + ", ".join(names)
            + ")"
        )

    async def _deltas(self, turn: Turn):
        """Answer deltas, then the question or files note the turn ended with."""
        emitted = False
        async for delta in self.executor.run_turn(turn):
            if delta.kind == "content":
                emitted = True
            yield delta.kind, delta.text
        if turn.question and not emitted:
            yield "content", turn.question
        note = self._closing_note(turn)
        if note:
            yield "content", note

    async def chat(self, request: Request):
        user = request.scope.get("user")
        owner = getattr(user, "username", "") or getattr(user, "display_name", "") or ""
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "request body is not valid JSON")
        try:
            turn, messages, stream = self.parse(body, owner)
        except RequestError as exc:
            return _error(400, str(exc))
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        if stream:

            async def events():
                answer: list[str] = []
                yield self._chunk(completion_id, {"role": "assistant", "content": ""})
                try:
                    async for kind, text in self._deltas(turn):
                        if kind == "content":
                            answer.append(text)
                            yield self._chunk(completion_id, {"content": text})
                        else:
                            yield self._chunk(completion_id, {"reasoning_content": text})
                except Exception as exc:  # noqa: BLE001 - sent to the client, sanitized
                    status, text = self._failure_text(exc)
                    LOG.warning("chat completion failed: %r", exc, extra={"event": "openai.error"})
                    error = {"message": text, "type": "agent_error", "code": status}
                    yield f"data: {json.dumps({'error': error})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                self._remember(
                    owner,
                    [*messages, {"role": "assistant", "content": "".join(answer)}],
                    turn.context_id,
                )
                yield self._chunk(completion_id, {}, finish="stop")
                yield "data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")

        answer, reasoning = [], []
        try:
            async for kind, text in self._deltas(turn):
                (answer if kind == "content" else reasoning).append(text)
        except Exception as exc:  # noqa: BLE001 - answered as a sanitized error
            status, text = self._failure_text(exc)
            LOG.warning("chat completion failed: %r", exc, extra={"event": "openai.error"})
            return _error(status, text, "agent_error")
        content = "".join(answer)
        self._remember(
            owner, [*messages, {"role": "assistant", "content": content}], turn.context_id
        )
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        }

    def models(self) -> dict[str, Any]:
        return {"object": "list", "data": [self.model_entry()]}

    def model_entry(self) -> dict[str, Any]:
        return {
            "id": self.model,
            "object": "model",
            "created": self.created,
            "owned_by": "lemonade-a2a",
            # Roughly tokens: chat apps use it to trim long histories.
            "context_length": max(1024, self.executor.max_input_chars // 4),
        }


def add_openai_routes(app: FastAPI, settings, executor) -> OpenAICompat:
    """Register ``/v1/models`` and ``/v1/chat/completions`` (call before the A2A routes,
    whose catch-all ``/{tenant}`` mount would otherwise answer ``/v1/...``)."""
    surface = OpenAICompat(settings, executor)

    @app.get("/v1/models", include_in_schema=False)
    async def list_models() -> dict[str, Any]:
        return surface.models()

    @app.get("/v1/models/{name:path}", include_in_schema=False)
    async def get_model(name: str):
        if name.split(".")[-1] != surface.model:
            return _error(404, f"model {name!r} not found", "not_found_error")
        return surface.model_entry()

    @app.post("/v1/chat/completions", include_in_schema=False)
    async def chat_completions(request: Request):
        return await surface.chat(request)

    return surface
