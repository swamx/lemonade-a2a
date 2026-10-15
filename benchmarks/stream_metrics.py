"""Transport metrics versus output metrics for streamed generations.

A streamed reply reaches the client as a series of events, and where those events
split the text is a property of the transport (Lemonade, the SSE framing, the A2A
adapter), not of the model. Counting events therefore measures the transport.
Counting tokens of the *assembled* text with one fixed tokenizer measures the
output, and it is identical whatever the event boundaries were.

This module keeps the two apart:

* ``chunks`` / ``chunks_per_s``: answer events and their rate (transport).
* ``output_tokens`` / ``tokens_per_s``: tokens of the assembled answer and their
  rate after the first answer event (output). It needs two or more events:
  ``tokens_per_s_basis`` names the estimator and ``tokens_per_s_measured`` is
  false when a single event made the rate 0.0, which is not zero-speed generation.
* ``ttft_reasoning_ms`` / ``ttft_ms`` / ``ttft_any_ms``: first reasoning output,
  first user-visible answer, first output of either kind. Reasoning events are
  never counted as answer events or answer tokens.

The default tokenizer is a dependency-free approximation, which is enough for a
direct-versus-adapter comparison because the same function is applied to both
paths. ``tiktoken:<encoding>`` is used when that package is installed.
"""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Callable

Tokenizer = Callable[[str], int]

_WORD = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def count_regex_tokens(text: str) -> int:
    """Words and single punctuation marks: stable, fast and independent of event splits."""
    return len(_WORD.findall(text))


@functools.cache
def get_tokenizer(name: str = "regex") -> Tokenizer:
    """``regex`` (default) or ``tiktoken:<encoding>`` (needs the optional package)."""
    if name == "regex":
        return count_regex_tokens
    kind, _, encoding = name.partition(":")
    if kind != "tiktoken":
        raise ValueError(f"unknown tokenizer {name!r}; use 'regex' or 'tiktoken:<encoding>'")
    try:
        import tiktoken
    except ImportError as error:
        raise ValueError("tokenizer 'tiktoken' needs: pip install tiktoken") from error
    encoder = tiktoken.get_encoding(encoding or "cl100k_base")
    return lambda text: len(encoder.encode(text))


class StreamTally:
    """Accumulates one generation's events, kept apart by kind (``answer`` or ``reasoning``)."""

    def __init__(self, tokenizer: Tokenizer = count_regex_tokens, *, start: float | None = None):
        self.tokenizer = tokenizer
        self.start = time.perf_counter() if start is None else start
        self.answer: list[str] = []
        self.reasoning: list[str] = []
        self.first_answer: float | None = None
        self.first_reasoning: float | None = None
        self.first_answer_text = ""

    def add(self, kind: str, text: str, now: float | None = None) -> None:
        if not text:
            return
        now = time.perf_counter() if now is None else now
        if kind == "reasoning":
            self.reasoning.append(text)
            self.first_reasoning = self.first_reasoning or now
        else:
            if self.first_answer is None:
                self.first_answer = now
                self.first_answer_text = text
            self.answer.append(text)

    @property
    def answer_text(self) -> str:
        return "".join(self.answer)

    @property
    def first_any(self) -> float | None:
        seen = [t for t in (self.first_answer, self.first_reasoning) if t is not None]
        return min(seen) if seen else None

    def result(self, why: str = "", end: float | None = None) -> dict:
        end = time.perf_counter() if end is None else end
        if self.first_answer is None:
            raise RuntimeError(f"stream produced no text chunks{(' (' + why + ')') if why else ''}")
        chunks = len(self.answer)
        window = max(end - self.first_answer, 1e-9)
        tokens = self.tokenizer(self.answer_text)
        # Tokens that arrived after the first event, over the same window as chunks/s.
        after_first = max(tokens - self.tokenizer(self.first_answer_text), 0)
        result = {
            "ttft_ms": (self.first_answer - self.start) * 1000,
            "total_ms": (end - self.start) * 1000,
            "chunks": chunks,
            "chunks_per_s": (chunks - 1) / window if chunks > 1 else 0.0,
            "output_tokens": tokens,
            "tokens_per_s": after_first / window if chunks > 1 else 0.0,
            # Label the estimator: tokens_per_s is a post-first-event rate and is 0.0 (not
            # measured) for a single-event reply, e.g. a buffered non-streaming answer.
            "tokens_per_s_basis": "post_first_event",
            "tokens_per_s_measured": chunks > 1,
            "output_chars": len(self.answer_text),
        }
        if self.first_any is not None:
            # Time to the first output of ANY kind. For a reasoning model this excludes the
            # random length of its thinking, which dominates ``ttft_ms``.
            result["ttft_any_ms"] = (self.first_any - self.start) * 1000
            result["think_chunks"] = len(self.reasoning)
            result["reasoning_tokens"] = self.tokenizer("".join(self.reasoning))
            if self.first_reasoning is not None:
                result["ttft_reasoning_ms"] = (self.first_reasoning - self.start) * 1000
        return result
