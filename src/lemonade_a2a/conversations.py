"""Per-caller conversation memory: earlier turns of an A2A context, and remote sessions.

A2A groups the messages of one conversation under a ``contextId``; each message can be a
new task. The adapter keeps the last few turns of each context so a follow-up ("and
tomorrow?") reaches the model with what came before, and, for an agent that consults
marketplace agents, which remote conversation (and which remote task waiting for input)
belongs to that context.

Entries are keyed by the authenticated caller *and* the context id, so one caller cannot
read another's history by reusing a context id. Memory is bounded: turns per context,
characters per context, number of contexts (least recently used are dropped) and an idle
time-to-live. Nothing is written to disk.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class RemoteSession:
    """One marketplace agent's side of a conversation."""

    context_id: str | None = None
    task_id: str | None = None  # set only while that agent waits for input


@dataclass
class Conversation:
    turns: list[dict[str, str]] = field(default_factory=list)
    remote: dict[str, RemoteSession] = field(default_factory=dict)
    pending: str | None = None  # id of the marketplace agent waiting for the caller's reply
    touched: float = field(default_factory=time.monotonic)


class ConversationStore:
    def __init__(
        self,
        max_turns: int = 10,
        *,
        max_chars: int = 20_000,
        max_contexts: int = 1000,
        ttl_seconds: float = 3600.0,
    ) -> None:
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.max_contexts = max_contexts
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[tuple[str, str], Conversation] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        while self._items:
            key, oldest = next(iter(self._items.items()))
            if oldest.touched > cutoff and len(self._items) <= self.max_contexts:
                break
            del self._items[key]

    def get(self, owner: str, context_id: str, *, create: bool = False) -> Conversation | None:
        if not context_id:
            return Conversation() if create else None
        key = (owner, context_id)
        self._expire()
        conversation = self._items.get(key)
        if conversation is None:
            if not create:
                return None
            conversation = self._items[key] = Conversation()
        conversation.touched = time.monotonic()
        self._items.move_to_end(key)
        self._expire()
        return conversation

    def history(self, owner: str, context_id: str) -> list[dict[str, str]]:
        """Earlier turns as chat messages, oldest first (empty when memory is off)."""
        if self.max_turns <= 0:
            return []
        conversation = self.get(owner, context_id)
        return list(conversation.turns) if conversation else []

    def record(self, owner: str, context_id: str, user: str, assistant: str) -> None:
        if self.max_turns <= 0 or not context_id:
            return
        conversation = self.get(owner, context_id, create=True)
        assert conversation is not None
        half = max(1, self.max_chars // 2)
        conversation.turns += [
            {"role": "user", "content": user[-half:]},
            {"role": "assistant", "content": assistant[:half]},
        ]
        turns = conversation.turns[-2 * self.max_turns :]
        while len(turns) > 2 and sum(len(t["content"]) for t in turns) > self.max_chars:
            turns = turns[2:]
        conversation.turns = turns
