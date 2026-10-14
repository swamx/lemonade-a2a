"""Backend contract tests: recorded streams from real servers, replayed against the client.

Each ``fixtures/<flavour>/<version-or-name>/<case>.sse`` is a raw OpenAI-style SSE body, and the
sibling ``.json`` says what the adapter must make of it. ``lemonade/<version>`` fixtures are
recorded from a real Lemonade with ``scripts/record_contract_fixtures.py``; supporting a new
Lemonade release starts by recording its fixtures. ``openai``, ``llama_cpp`` and ``vllm`` are
other OpenAI-compatible servers, which prove the backend contract is not Lemonade-specific.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from a2a.types import Message, Part, Role, TaskState

from lemonade_a2a import registry
from lemonade_a2a.executor import ARTIFACT_NAME, LemonadeAgentExecutor
from lemonade_a2a.lemonade_client import BackendStreamError, LemonadeClient
from tests.telemetry_helpers import Recorder

FIXTURES = Path(__file__).parent / "fixtures"
CASES = sorted(FIXTURES.rglob("*.sse"))


def _id(path: Path) -> str:
    return path.relative_to(FIXTURES).with_suffix("").as_posix()


def _expected(path: Path) -> dict:
    return json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))


def _client(path: Path, telemetry=None) -> LemonadeClient:
    body = path.read_bytes()
    client = LemonadeClient(
        "http://backend/v1", "m", **({"telemetry": telemetry} if telemetry else {})
    )
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    )
    return client


class Queue:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(event)


def _context():
    message = Message(message_id="m", role=Role.ROLE_USER, parts=[Part(text="hi")])
    return SimpleNamespace(
        message=message,
        task_id="t",
        context_id="c",
        get_user_input=lambda: "hi",
        call_context=SimpleNamespace(state={}),
    )


def test_every_fixture_has_expectations_and_vice_versa() -> None:
    sse = {p.with_suffix("") for p in FIXTURES.rglob("*.sse")}
    expectations = {p.with_suffix("") for p in FIXTURES.rglob("*.json")}

    assert sse == expectations and sse


def test_each_flavour_has_a_normal_completion_case() -> None:
    flavours = {p.relative_to(FIXTURES).parts[0] for p in CASES}
    completing = {
        p.relative_to(FIXTURES).parts[0] for p in CASES if _expected(p)["outcome"] == "completed"
    }

    assert {"lemonade", "openai", "llama_cpp", "vllm"} <= flavours
    # Lemonade's normal cases are recorded from the real server; at minimum every flavour
    # that is not Lemonade must show a completion.
    assert {"openai", "llama_cpp", "vllm"} <= completing


def test_every_tested_lemonade_version_has_recorded_fixtures() -> None:
    """A version in the manifest's ``tested`` list is a claim; recorded streams back it."""
    for version in registry.manifest()["lemonade"]["tested"]:
        assert (FIXTURES / "lemonade" / version).is_dir(), f"no fixtures for Lemonade {version}"


@pytest.mark.parametrize("path", CASES, ids=_id)
async def test_client_parses_the_recorded_stream(path: Path) -> None:
    expected = _expected(path)
    recorder = Recorder()
    client = _client(path, recorder.telemetry)

    try:
        deltas = [d async for d in client.stream_events([{"role": "user", "content": "hi"}])]
    except BackendStreamError:
        assert expected["outcome"] == "failed" and not expected["answer"]
        return

    answer = "".join(d.text for d in deltas if d.kind == "content")
    thinking = "".join(d.text for d in deltas if d.kind == "reasoning")
    assert answer == expected["answer"]
    assert thinking == expected["reasoning"]
    if expected["usage"]:
        assert (
            recorder.sum("gen_ai.client.token.usage", **{"gen_ai.token.type": "input"})
            == expected["usage"]["input"]
        )
        assert (
            recorder.sum("gen_ai.client.token.usage", **{"gen_ai.token.type": "output"})
            == expected["usage"]["output"]
        )
    else:
        assert recorder.total("gen_ai.client.token.usage") == 0


@pytest.mark.parametrize("path", CASES, ids=_id)
async def test_executor_reaches_the_expected_outcome(path: Path) -> None:
    expected = _expected(path)
    queue = Queue()

    await LemonadeAgentExecutor(_client(path)).execute(_context(), queue)

    statuses = [e for e in queue.events if getattr(e, "status", None) and e.HasField("status")]
    final = statuses[-1].status
    artifacts = [e for e in queue.events if hasattr(e, "last_chunk") and e.HasField("artifact")]
    text = "".join(
        p.text for e in artifacts if e.artifact.name == ARTIFACT_NAME for p in e.artifact.parts
    )
    if expected["outcome"] == "completed":
        assert final.state == TaskState.TASK_STATE_COMPLETED
        assert text == expected["answer"]  # the answer, never the thinking
        assert artifacts[-1].last_chunk is True
    else:
        assert final.state == TaskState.TASK_STATE_FAILED
        assert final.message.parts[0].text == expected["failure"]
        assert not text or artifacts[-1].last_chunk is True  # never left open
