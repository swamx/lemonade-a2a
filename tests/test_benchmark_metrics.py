"""Benchmark metrics: transport (chunks) versus output (tokens) and reasoning versus answer.

Splitting the same text into more stream events must change the transport figures
and nothing else. These tests need no server: the stream parsers run against
canned SSE bodies.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path

import httpx
import pytest

BENCH = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCH))

import stream_metrics
from stream_metrics import StreamTally, count_regex_tokens, get_tokenizer


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BENCH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


evidence = _load("benchmark_evidence")
budget = _load("check_budget")

TEXT = "Hello world, this is a local model. It answers in two sentences!"


def _split(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _tally(pieces: list[str], reasoning: list[str] = ()) -> dict:
    tally = StreamTally(start=0.0)
    now = 1.0
    for piece in reasoning:
        tally.add("reasoning", piece, now=now)
        now += 0.1
    for piece in pieces:
        tally.add("answer", piece, now=now)
        now += 0.1
    return tally.result(end=now)


@pytest.mark.parametrize("size", [1, 2, 3, 7, len(TEXT)])
def test_splitting_the_same_text_changes_chunks_but_not_the_output(size: int) -> None:
    whole = _tally([TEXT])
    split = _tally(_split(TEXT, size))

    assert split["output_tokens"] == whole["output_tokens"] == count_regex_tokens(TEXT)
    assert split["output_chars"] == whole["output_chars"] == len(TEXT)
    assert split["chunks"] == len(_split(TEXT, size))
    assert whole["chunks"] == 1


def test_hello_world_as_one_event_or_two_is_the_same_output() -> None:
    one = _tally(["Hello world"])
    two = _tally(["Hello", " world"])

    assert one["output_tokens"] == two["output_tokens"] == 2
    assert (one["chunks"], two["chunks"]) == (1, 2)
    # The transport figure moves with the split; that is exactly why it is not the output metric.
    assert one["chunks_per_s"] == 0.0 < two["chunks_per_s"]


def test_single_event_rate_is_labelled_unmeasured_not_zero_speed() -> None:
    one = _tally(["Hello world"])
    two = _tally(["Hello", " world"])

    assert one["tokens_per_s_basis"] == two["tokens_per_s_basis"] == "post_first_event"
    assert one["tokens_per_s_measured"] is False
    assert two["tokens_per_s_measured"] is True


def test_a_word_split_across_events_is_still_one_token() -> None:
    assert _tally(["Hel", "lo"])["output_tokens"] == _tally(["Hello"])["output_tokens"] == 1


def test_reasoning_is_not_counted_as_answer() -> None:
    result = _tally(["The ", "answer"], reasoning=["let me ", "think about it ", "carefully"])

    assert result["chunks"] == 2
    assert result["output_tokens"] == 2
    assert result["think_chunks"] == 3
    assert result["reasoning_tokens"] == 6
    assert result["ttft_reasoning_ms"] == pytest.approx(1000)
    assert result["ttft_any_ms"] == pytest.approx(1000)
    assert result["ttft_ms"] == pytest.approx(1300)  # the first user-visible answer
    assert result["ttft_reasoning_ms"] < result["ttft_ms"]


def test_no_reasoning_means_no_reasoning_ttft() -> None:
    result = _tally(["a", "b"])

    assert "ttft_reasoning_ms" not in result
    assert result["think_chunks"] == 0
    assert result["ttft_any_ms"] == result["ttft_ms"]


def test_a_stream_of_only_reasoning_is_not_an_answer() -> None:
    tally = StreamTally(start=0.0)
    tally.add("reasoning", "thinking", now=1.0)

    with pytest.raises(RuntimeError, match="no text chunks"):
        tally.result(why="reasoning only", end=2.0)


def test_empty_text_is_ignored() -> None:
    tally = StreamTally(start=0.0)
    tally.add("answer", "", now=1.0)
    tally.add("reasoning", "", now=1.0)

    assert tally.first_answer is None and tally.first_reasoning is None


def test_tokenizers() -> None:
    assert get_tokenizer("regex") is count_regex_tokens
    with pytest.raises(ValueError, match="unknown tokenizer"):
        get_tokenizer("made-up")
    if importlib.util.find_spec("tiktoken") is None:
        with pytest.raises(ValueError, match="pip install tiktoken"):
            get_tokenizer("tiktoken:cl100k_base")
    else:
        assert get_tokenizer("tiktoken:cl100k_base")("Hello world") == 2
    assert stream_metrics.count_regex_tokens("") == 0


# -- the real stream parsers, against canned bodies -------------------------------------


def _client(body: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _openai_body(contents: list[str], reasoning: list[str] = ()) -> str:
    deltas = [{"reasoning_content": r} for r in reasoning] + [{"content": c} for c in contents]
    events = [{"choices": [{"index": 0, "delta": d}]} for d in deltas]
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def _a2a_body(contents: list[str], reasoning: list[str] = ()) -> str:
    def update(name: str, text: str) -> dict:
        return {
            "result": {
                "artifactUpdate": {
                    "taskId": "t1",
                    "artifact": {"name": name, "parts": [{"text": text}]},
                }
            }
        }

    events = [update("lemonade-reasoning", r) for r in reasoning]
    events += [update("lemonade-response", c) for c in contents]
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def _args() -> Namespace:
    return Namespace(
        model="m",
        prompt="hi",
        direct_url="http://direct.test/v1/chat/completions",
        a2a_url="http://a2a.test",
        tokenizer="regex",
    )


@pytest.mark.parametrize("pieces", [[TEXT], _split(TEXT, 5), _split(TEXT, 1)])
async def test_direct_and_a2a_agree_on_output_for_any_event_split(pieces: list[str]) -> None:
    async with _client(_openai_body(pieces)) as client:
        direct = await evidence.direct_stream(client, _args())
    async with _client(_a2a_body(pieces)) as client:
        a2a = await evidence.a2a_stream(client, _args())

    for result in (direct, a2a):
        assert result["output_tokens"] == count_regex_tokens(TEXT)
        assert result["output_chars"] == len(TEXT)
        assert result["chunks"] == len(pieces)
    assert direct["output_tokens"] == a2a["output_tokens"]


async def test_same_text_split_differently_per_path_still_compares_equal() -> None:
    """The confound itself: the adapter re-chunks, the tokens do not move."""
    async with _client(_openai_body(_split(TEXT, 2))) as client:
        direct = await evidence.direct_stream(client, _args())
    async with _client(_a2a_body([TEXT])) as client:
        a2a = await evidence.a2a_stream(client, _args())

    assert direct["chunks"] != a2a["chunks"]
    assert direct["output_tokens"] == a2a["output_tokens"]


async def test_reasoning_stays_out_of_the_answer_on_both_paths() -> None:
    thinking, answer = ["let me ", "think"], ["The ", "answer"]
    async with _client(_openai_body(answer, thinking)) as client:
        direct = await evidence.direct_stream(client, _args())
    async with _client(_a2a_body(answer, thinking)) as client:
        a2a = await evidence.a2a_stream(client, _args())

    for result in (direct, a2a):
        assert result["chunks"] == 2
        assert result["output_tokens"] == 2
        assert result["think_chunks"] == 2
        assert "ttft_reasoning_ms" in result


async def test_a_reasoning_only_stream_fails_with_a_reason() -> None:
    async with _client(_openai_body([], ["thinking"])) as client:
        with pytest.raises(RuntimeError, match="reasoning_chunks=1"):
            await evidence.direct_stream(client, _args())


# -- the budget gate ------------------------------------------------------------------


def _report(chunks: tuple[float, float], tokens: tuple[float, float] | None) -> dict:
    def pair(d: float, a: float) -> dict:
        return {"direct": {"median": d}, "a2a": {"median": a}}

    report = {
        "ttft_ms": pair(100, 105),
        "total_ms": pair(1000, 1010),
        "chunks_per_s": pair(*chunks),
        "cancellation": {"result": "pass"},
    }
    if tokens is not None:
        report["tokens_per_s"] = pair(*tokens)
    return report


def _throughput(report: dict) -> tuple[bool, str]:
    _name, ok, detail = next(c for c in budget.evaluate(report) if c[0] == "Streaming throughput")
    return ok, detail


def test_budget_gates_on_tokens_not_on_how_the_transport_split_the_text() -> None:
    # The adapter coalesced events (half the chunks/s) but the model output rate is unchanged.
    ok, detail = _throughput(_report(chunks=(20, 10), tokens=(40, 40)))

    assert ok
    assert "tokens/s" in detail


def test_budget_still_fails_a_real_output_slowdown() -> None:
    ok, _ = _throughput(_report(chunks=(20, 20), tokens=(40, 20)))

    assert not ok


def test_budget_falls_back_to_chunks_for_older_reports() -> None:
    ok, detail = _throughput(_report(chunks=(20, 10), tokens=None))

    assert not ok
    assert "chunks/s" in detail
