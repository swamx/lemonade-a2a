"""DNS SRV discovery, multi-turn conversations, file parts and multi-agent delegation."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import Artifact, Message, Part, StreamResponse, Task, TaskState, TaskStatus
from a2a.utils.errors import ContentTypeNotSupportedError, InvalidParamsError
from fastapi.testclient import TestClient
from google.protobuf.struct_pb2 import Struct

from lemonade_a2a import builtin
from lemonade_a2a.client import A2AClient
from lemonade_a2a.client.models import AgentReply
from lemonade_a2a.config import Settings
from lemonade_a2a.conversations import ConversationStore
from lemonade_a2a.delegation import (
    FILES_ARTIFACT_NAME,
    Call,
    MarketplaceAgentExecutor,
    parse_mentions,
    parse_plan,
)
from lemonade_a2a.discovery import dns as dns_discovery
from lemonade_a2a.discovery.dns import ServiceTarget, lookup_service, txt_settings
from lemonade_a2a.discovery.marketplace import (
    Marketplace,
    MarketplaceAgent,
    MarketplaceError,
    listing_candidates,
    srv_domain,
)
from lemonade_a2a.executor import LemonadeAgentExecutor, Turn, accepted_media_types
from lemonade_a2a.lemonade_client import Delta
from lemonade_a2a.server import build_agent_card, create_app
from tests.live import A2A_HEADERS, free_port, serve
from tests.mock_lemonade import app as mock_app
from tests.test_marketplace import card, mock_market

# -- DNS SRV ------------------------------------------------------------------------------


def fake_dns(records: dict[tuple[str, str], list]):
    async def resolve(name: str, rdtype: str) -> list:
        return records.get((name, rdtype), [])

    return resolve


async def test_srv_targets_are_ordered_and_carry_the_txt_path() -> None:
    resolver = fake_dns(
        {
            ("_a2a-registry._tcp.example.com", "SRV"): [
                (20, 0, 443, "backup.example.com."),
                (10, 5, 443, "low.example.com."),
                (10, 50, 8443, "high.example.com."),
                (0, 0, 0, "."),  # "not offered"
                ("bad", 0, 443, "x.example.com."),
            ],
            ("_a2a-registry._tcp.example.com", "TXT"): ["txtvers=1", "path=/api/agents"],
        }
    )
    targets = await lookup_service("example.com", "_a2a-registry._tcp", resolver)
    assert [t.host for t in targets] == [
        "high.example.com",
        "low.example.com",
        "backup.example.com",
    ]
    assert targets[0].path == "/api/agents"
    assert targets[0].origin("https") == "https://high.example.com:8443"
    assert targets[1].origin("https") == "https://low.example.com"
    assert ServiceTarget(0, 0, 80, "::1").origin("http") == "http://[::1]"


async def test_a_txt_path_that_is_not_a_plain_path_is_ignored() -> None:
    resolver = fake_dns(
        {
            ("_a2a._tcp.example.com", "SRV"): [(0, 0, 443, "a.example.com")],
            ("_a2a._tcp.example.com", "TXT"): ["path=/../../etc/passwd"],
        }
    )
    [target] = await lookup_service("example.com", "_a2a._tcp", resolver)
    assert target.path == ""
    assert txt_settings(["noequals", 5, "Path=/x", "path=/y"]) == {"path": "/x"}


def test_which_sources_use_dns() -> None:
    assert srv_domain("Example.COM") == "example.com"
    assert srv_domain("dns:example.com.") == "example.com"
    assert srv_domain("https://example.com") is None
    assert srv_domain("example.com:8443") is None
    assert srv_domain("localhost") is None
    assert listing_candidates("dns:example.com") == []
    with pytest.raises(MarketplaceError):
        srv_domain("dns:not a domain")
    with pytest.raises(ValueError, match="MARKETPLACE"):
        Settings(marketplace="dns:-bad-")


async def test_a_domain_is_found_through_its_srv_record_first(monkeypatch) -> None:
    seen: list[httpx.Request] = []
    routes = {
        "https://registry.example.net/api/agents": [card("Weather", "https://w.example/a2a")],
    }
    mock_market(monkeypatch, routes, seen)
    resolver = fake_dns(
        {
            ("_a2a-registry._tcp.example.com", "SRV"): [(0, 0, 443, "registry.example.net.")],
            ("_a2a-registry._tcp.example.com", "TXT"): ["path=/api/agents"],
        }
    )
    market = Marketplace("example.com", dns_resolver=resolver, api_key="k")

    [agent] = await market.agents()

    assert agent.source == "example.com"
    assert str(seen[0].url) == "https://registry.example.net/api/agents"
    # The key belongs to the domain the operator named, not to a host DNS pointed at.
    assert seen[0].headers.get("authorization") is None


async def test_a_single_agent_srv_record_and_dns_only_sources(monkeypatch) -> None:
    routes = {
        "https://agent.example.net:8443/.well-known/agent-card.json": card(
            "Solo", "https://agent.example.net:8443/a2a"
        )
    }
    mock_market(monkeypatch, routes)
    resolver = fake_dns({("_a2a._tcp.example.com", "SRV"): [(0, 0, 8443, "agent.example.net")]})
    assert [a.id for a in await Marketplace("dns:example.com", dns_resolver=resolver).agents()] == [
        "solo"
    ]
    with pytest.raises(MarketplaceError, match="no agent listing"):
        await Marketplace("dns:example.org", dns_resolver=fake_dns({})).agents()


async def test_srv_records_cannot_point_into_the_private_network(monkeypatch) -> None:
    mock_market(monkeypatch, {})
    resolver = fake_dns({("_a2a-registry._tcp.example.com", "SRV"): [(0, 0, 443, "10.1.2.3")]})
    market = Marketplace("dns:example.com", dns_resolver=resolver)
    with pytest.raises(MarketplaceError):
        await market.agents()
    assert any("SRV" in problem and "10.1.2.3" in problem for problem in market.problems)


async def test_the_system_resolver_reads_dnspython_answers(monkeypatch) -> None:
    import dns.asyncresolver
    import dns.resolver

    srv = SimpleNamespace(
        priority=1, weight=2, port=443, target=SimpleNamespace(to_text=lambda: "a.example.")
    )
    txt = SimpleNamespace(strings=[b"path=", b"/x"])

    async def resolve(name, rdtype, lifetime):
        if name == "missing.example":
            raise dns.resolver.NXDOMAIN()
        if name == "broken.example":
            raise dns.resolver.LifetimeTimeout(timeout=1.0, errors={})
        return [srv] if rdtype == "SRV" else [txt]

    monkeypatch.setattr(dns.asyncresolver, "resolve", resolve)
    assert await dns_discovery.system_resolver("x.example", "SRV") == [(1, 2, 443, "a.example.")]
    assert await dns_discovery.system_resolver("x.example", "TXT") == ["path=/x"]
    assert await dns_discovery.system_resolver("missing.example", "SRV") == []
    assert await dns_discovery.system_resolver("broken.example", "SRV") == []


# -- conversation memory ------------------------------------------------------------------


def test_conversations_are_bounded_and_per_caller() -> None:
    store = ConversationStore(max_turns=2, max_chars=40, max_contexts=2)
    for n in range(3):
        store.record("alice", "c1", f"q{n}", f"a{n}")
    assert [t["content"] for t in store.history("alice", "c1")] == ["q1", "a1", "q2", "a2"]
    assert store.history("bob", "c1") == []  # same context id, other caller
    store.record("alice", "c1", "x" * 30, "y" * 30)  # each side trimmed to max_chars / 2
    assert [len(t["content"]) for t in store.history("alice", "c1")] == [20, 20]
    store.record("alice", "c2", "q", "a")
    store.record("alice", "c3", "q", "a")
    assert len(store) == 2 and store.history("alice", "c1") == []  # least recently used
    store.record("alice", "", "q", "a")  # no context id: nothing to remember
    assert store.get("alice", "", create=True) is not None
    assert len(store) == 2


def test_conversations_expire_and_can_be_off() -> None:
    store = ConversationStore(max_turns=5, ttl_seconds=0)
    store.record("a", "c", "q", "a")
    assert store.history("a", "c") == []
    off = ConversationStore(max_turns=0)
    off.record("a", "c", "q", "a")
    assert off.history("a", "c") == [] and len(off) == 0


class RecordingBackend:
    """Answers "answer <n>" and remembers every message list it was given."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def stream_events(self, messages):
        self.calls.append(messages)
        yield Delta("content", f"answer {len(self.calls)}")

    async def aclose(self) -> None:
        pass


def send(client: TestClient, text: str, context: str, key: str | None = None) -> dict:
    headers = dict(A2A_HEADERS)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "message": {
            "messageId": f"m-{text}-{context}-{key}",
            "contextId": context,
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
    }
    response = client.post("/message:send", content=json.dumps(body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_follow_up_in_the_same_context_sees_the_earlier_turns(monkeypatch) -> None:
    backend = RecordingBackend()
    monkeypatch.setattr(builtin, "create_backend", lambda settings, telemetry: backend)
    settings = Settings(api_keys="alice:ka,bob:kb")
    with TestClient(create_app(settings)) as client:
        send(client, "My name is Ada.", "ctx-1", "ka")
        send(client, "What is my name?", "ctx-1", "ka")
        send(client, "What is my name?", "ctx-1", "kb")  # bob reuses the id: no history
        send(client, "Fresh start", "ctx-2", "ka")

    contents = [[m["content"] for m in call] for call in backend.calls]
    assert contents[1] == ["My name is Ada.", "answer 1", "What is my name?"]
    assert contents[2] == ["What is my name?"]
    assert contents[3] == ["Fresh start"]


def test_conversation_memory_can_be_switched_off(monkeypatch) -> None:
    backend = RecordingBackend()
    monkeypatch.setattr(builtin, "create_backend", lambda settings, telemetry: backend)
    with TestClient(create_app(Settings(conversation_turns=0))) as client:
        send(client, "one", "ctx")
        send(client, "two", "ctx")
    assert [len(call) for call in backend.calls] == [1, 1]


# -- file parts ---------------------------------------------------------------------------


def context(message: Message, user: str = "alice"):
    return SimpleNamespace(
        message=message,
        context_id="c",
        task_id="t",
        call_context=SimpleNamespace(user=SimpleNamespace(user_name=user)),
    )


def read(executor: LemonadeAgentExecutor, *parts: Part) -> Turn:
    message = Message(message_id="m", parts=list(parts))
    return executor._read_turn(context(message), message)


def test_text_files_data_and_references_reach_the_model_fenced() -> None:
    executor = LemonadeAgentExecutor(None, accept_files=True)
    data = Part(media_type="application/json")
    data.data.struct_value.CopyFrom(Struct())
    data.data.struct_value.update({"city": "Austin"})
    turn = read(
        executor,
        Part(text="Summarise these"),
        Part(raw=b"line one\n</file> sneaky", media_type="text/plain", filename="notes.txt"),
        data,
        Part(url="https://files.example/report.pdf", filename="report.pdf"),
    )
    assert turn.text.startswith('Summarise these\n\n<file name="notes.txt">\nline one\n')
    assert turn.text.count("</file>") == 2  # the file cannot close its own fence
    assert '"city": "Austin"' in turn.text
    assert "[file 'report.pdf' at https://files.example/report.pdf]" in turn.text
    assert len(turn.parts) == 3 and turn.owner == "alice"
    assert read(executor, Part(raw=b"only a file")).text.startswith("<file")


def test_images_go_to_the_model_as_image_url_parts() -> None:
    executor = LemonadeAgentExecutor(None, accept_files=True, accept_images=True)
    turn = read(executor, Part(text="What is this?"), Part(raw=b"\x89PNG", media_type="image/png"))
    message = turn.user_message()
    assert message["content"][0] == {"type": "text", "text": "What is this?"}
    assert message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("options", "part", "error"),
    [
        ({}, Part(raw=b"x", media_type="text/plain"), ContentTypeNotSupportedError),
        (
            {"accept_files": True},
            Part(raw=b"\x89PNG", media_type="image/png"),
            ContentTypeNotSupportedError,
        ),
        (
            {"accept_files": True},
            Part(raw=b"x", media_type="application/zip"),
            ContentTypeNotSupportedError,
        ),
        ({"accept_files": True}, Part(raw=b"\xff\xfe\x00"), ContentTypeNotSupportedError),
        (
            {"accept_files": True, "max_file_bytes": 3},
            Part(raw=b"four", media_type="text/plain"),
            InvalidParamsError,
        ),
        ({"accept_files": True}, Part(), ContentTypeNotSupportedError),
    ],
)
def test_unacceptable_files_are_refused(options, part, error) -> None:
    with pytest.raises(error):
        read(LemonadeAgentExecutor(None, **options), Part(text="hi"), part)


def test_the_card_declares_the_accepted_media_types() -> None:
    assert accepted_media_types(files=False, images=False) == ["text"]
    card_ = build_agent_card(Settings(accept_files=True, accept_images=True))
    assert {"text/plain", "application/json", "image/png"} <= set(card_.default_input_modes)
    assert "image/png" not in build_agent_card(Settings(accept_files=True)).default_input_modes
    with pytest.raises(ValueError, match="FILE_IMAGES"):
        Settings(accept_images=True)
    with pytest.raises(ValueError, match="CONVERSATION_TURNS"):
        Settings(conversation_turns=-1)


# -- several delegations per task ---------------------------------------------------------


def agent(agent_id: str) -> MarketplaceAgent:
    return MarketplaceAgent(agent_id, agent_id.title(), "", "u", "m", card(agent_id, "u"))


class PlanningBackend:
    """Router answers come from ``plans`` in order; anything else echoes the prompt."""

    def __init__(self, *plans: str) -> None:
        self.plans = list(plans)
        self.prompts: list[str] = []

    async def stream_events(self, messages):
        if "You route requests" in messages[0]["content"]:
            self.prompts.append(messages[0]["content"])
            yield Delta("content", self.plans.pop(0) if self.plans else '{"calls": []}')
        else:
            yield Delta("content", "LOCAL:" + str(messages[-1]["content"]))

    async def aclose(self) -> None:
        pass


class ScriptedMarketplace:
    """Each agent answers from ``answers[agent id]`` (a list consumed in order)."""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    async def agents(self):
        return [agent(agent_id) for agent_id in self.answers]

    async def ask(self, target, text, **kwargs):
        self.calls.append({"agent": target.id, "text": text, **kwargs})
        answer = self.answers[target.id].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def done(text: str = "", *, context_id: str = "remote-ctx", files=()) -> AgentReply:
    reply = AgentReply()
    artifact = Artifact(artifact_id="a", parts=[Part(text=text), *files])
    reply.add(
        StreamResponse(
            task=Task(
                id="rt",
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                artifacts=[artifact],
            )
        )
    )
    return reply


def waiting(question: str) -> AgentReply:
    reply = AgentReply()
    status = TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED)
    status.message.parts.append(Part(text=question))
    reply.add(StreamResponse(task=Task(id="rt-wait", context_id="remote-ctx", status=status)))
    return reply


async def run(executor, turn: Turn) -> str:
    return "".join([d.text async for d in executor._deltas(turn) if d.kind == "content"])


def test_parse_plan_and_mentions() -> None:
    ids = {"a", "b", "c"}
    assert parse_plan('{"calls": [{"agent": "a", "message": "x"}, {"agent": "B"}]}', ids, 5) == [
        ("a", "x"),
        ("b", ""),
    ]
    assert parse_plan('[{"agent": "a"}, {"agent": "a"}, {"agent": "z"}, 7]', ids, 5) == [("a", "")]
    assert parse_plan('{"calls": [{"agent": "a"}, {"agent": "b"}, {"agent": "c"}]}', ids, 2) == [
        ("a", ""),
        ("b", ""),
    ]
    assert parse_plan('{"calls": "nope"}', ids, 3) == []
    assert parse_plan("[broken", ids, 3) == []
    assert parse_mentions("@a @b, @a: hello", ids) == (["a", "b"], "hello")
    assert parse_mentions("@zed hello", ids) == ([], "@zed hello")
    assert parse_mentions("@ab hello", {"a", "ab"}) == (["ab"], "hello")


async def test_planned_agents_run_in_rounds_within_the_budget() -> None:
    backend = PlanningBackend(
        '{"calls": [{"agent": "flights", "message": "MAD-LHR"}, {"agent": "hotels", "message": "London"}]}',
        '{"calls": [{"agent": "weather", "message": "London next week"}, {"agent": "flights"}]}',
    )
    market = ScriptedMarketplace(
        {"flights": [done("F1")], "hotels": [done("H1")], "weather": [done("W1")]}
    )
    executor = MarketplaceAgentExecutor(backend, market, max_delegations=3)

    content = await run(executor, Turn(text="Plan my London trip", context_id="c"))

    assert [call["agent"] for call in market.calls] == ["flights", "hotels", "weather"]
    assert "F1" in backend.prompts[1] and "H1" in backend.prompts[1]  # round 2 sees round 1
    assert "at most 1 more" in backend.prompts[1]
    for answer in ("F1", "H1", "W1"):
        assert f">\n{answer}\n</agent_reply>" in content


async def test_one_delegation_means_no_second_round() -> None:
    backend = PlanningBackend('{"calls": [{"agent": "a"}, {"agent": "b"}]}')
    market = ScriptedMarketplace({"a": [done("A")], "b": [done("B")]})
    executor = MarketplaceAgentExecutor(backend, market, max_delegations=1, reply_mode="relay")
    assert await run(executor, Turn(text="q")) == "A"
    assert len(backend.prompts) == 1 and [c["agent"] for c in market.calls] == ["a"]


async def test_mentioning_several_agents_asks_them_all_and_relays_each() -> None:
    market = ScriptedMarketplace({"a": [done("A says")], "b": [done("B says")]})
    executor = MarketplaceAgentExecutor(PlanningBackend(), market, reply_mode="relay")
    assert await run(executor, Turn(text="@a @b ping")) == "A:\nA says\n\nB:\nB says"
    assert [c["text"] for c in market.calls] == ["ping", "ping"]


async def test_files_are_forwarded_and_returned_files_become_an_artifact() -> None:
    returned = Part(raw=b"%PDF", media_type="application/pdf", filename="ticket.pdf")
    market = ScriptedMarketplace({"a": [done("booked", files=[returned])]})
    executor = MarketplaceAgentExecutor(PlanningBackend(), market, reply_mode="relay")
    sent = Part(raw=b"passport", media_type="text/plain")
    turn = Turn(text="@a book it", parts=[sent])

    assert (await run(executor, turn)).startswith("booked")
    assert list(market.calls[0]["parts"]) == [sent]
    assert turn.artifacts == [(FILES_ARTIFACT_NAME, [returned])]


async def test_a_remote_question_pauses_the_task_and_the_reply_resumes_that_agent() -> None:
    market = ScriptedMarketplace(
        {"weather": [waiting("Which city?"), done("Sunny in Austin"), done("Rain tomorrow")]}
    )
    executor = MarketplaceAgentExecutor(PlanningBackend(), market, reply_mode="relay")

    first = Turn(text="@weather forecast", owner="alice", context_id="c")
    assert await run(executor, first) == ""
    assert first.question == "Weather needs more information: Which city?"

    second = Turn(text="Austin", owner="alice", context_id="c")
    assert await run(executor, second) == "Sunny in Austin"
    assert (market.calls[1]["task_id"], market.calls[1]["context_id"]) == ("rt-wait", "remote-ctx")

    third = Turn(text="@weather and tomorrow?", owner="alice", context_id="c")
    assert await run(executor, third) == "Rain tomorrow"
    assert market.calls[2]["task_id"] is None  # a new task in the same remote conversation
    assert market.calls[2]["context_id"] == "remote-ctx"


async def test_consult_without_a_conversation_still_works() -> None:
    market = ScriptedMarketplace({"a": [done("")]})
    executor = MarketplaceAgentExecutor(PlanningBackend(), market)
    result = await executor.consult(Call(agent("a"), "hi"))
    assert not result.ok and "empty" in result.text
    with pytest.raises(ValueError):
        MarketplaceAgentExecutor(PlanningBackend(), market, max_delegations=0)


def test_new_settings_come_from_the_environment() -> None:
    settings = Settings.from_env(
        {
            "LEMONADE_A2A_MAX_DELEGATIONS": "5",
            "LEMONADE_A2A_CONVERSATION_TURNS": "4",
            "LEMONADE_A2A_FILES": "1",
            "LEMONADE_A2A_FILE_IMAGES": "1",
            "LEMONADE_A2A_MAX_FILE_BYTES": "1000",
        }
    )
    assert (settings.max_delegations, settings.conversation_turns) == (5, 4)
    assert (settings.accept_files, settings.accept_images, settings.max_file_bytes) == (
        True,
        True,
        1000,
    )
    with pytest.raises(ValueError, match="MAX_DELEGATIONS"):
        Settings(max_delegations=0)


# -- end to end: a remote agent that asks back, with files both ways ----------------------


class AskOnceAgent(AgentExecutor):
    """Asks "Which city?" first; on the reply, answers and attaches a file."""

    def __init__(self) -> None:
        self.received: list[list[str]] = []

    async def execute(self, context, event_queue) -> None:
        message = context.message
        self.received.append([p.WhichOneof("content") for p in message.parts])
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[message],
                )
            )
            await updater.requires_input(updater.new_agent_message([Part(text="Which city?")]))
            return
        await updater.start_work()
        city = context.get_user_input().strip().upper()
        await updater.add_artifact([Part(text=f"SUNNY IN {city}")], name="answer", last_chunk=True)
        await updater.add_artifact(
            [Part(raw=b"forecast", media_type="text/plain", filename="forecast.txt")],
            name="files",
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:
        pass


async def test_a_multi_turn_conversation_across_two_agents(monkeypatch) -> None:
    mock_port, weather_port, market_port, caller_port = (free_port() for _ in range(4))
    lemonade = f"http://127.0.0.1:{mock_port}/v1"
    remote = AskOnceAgent()
    weather = Settings(
        lemonade_base_url=lemonade,
        port=weather_port,
        public_url=f"http://127.0.0.1:{weather_port}",
        agent_name="Weather Agent",
        accept_files=True,
    )
    from fastapi import FastAPI

    market = FastAPI()

    @market.get("/.well-known/agent-registry.json")
    async def registry() -> dict:
        return {"agents": [{"id": "weather", "url": f"http://127.0.0.1:{weather_port}"}]}

    caller = Settings(
        lemonade_base_url=lemonade,
        port=caller_port,
        public_url=f"http://127.0.0.1:{caller_port}",
        marketplace=f"127.0.0.1:{market_port}",
        marketplace_allow_local=True,
        marketplace_reply="relay",
        accept_files=True,
    )
    note = Part(raw=b"I am travelling", media_type="text/plain", filename="note.txt")
    with (
        serve(mock_app, mock_port),
        serve(create_app(weather, executor=remote), weather_port),
        serve(market, market_port),
        serve(create_app(caller), caller_port),
    ):
        async with A2AClient(f"http://127.0.0.1:{caller_port}", allow_local=True) as client:
            first = await client.ask("@weather forecast", parts=[note])
            assert first.state == "TASK_STATE_INPUT_REQUIRED"
            assert first.status_text == "Weather Agent needs more information: Which city?"

            second = await client.ask("Austin", context_id=first.context_id, task_id=first.task_id)

    assert second.state == "TASK_STATE_COMPLETED"
    assert second.text.startswith("SUNNY IN AUSTIN")
    assert [part.filename for part in second.files] == ["forecast.txt"]
    assert second.task_id == first.task_id  # the caller's own task was continued
    assert remote.received == [["text", "raw"], ["text"]]  # the file was forwarded once


def test_only_parts_the_remote_card_accepts_are_forwarded() -> None:
    from lemonade_a2a.delegation import accepted_parts

    text_only = agent("a")
    text_only.card["defaultInputModes"] = ["text"]
    files = MarketplaceAgent(
        "f", "F", "", "u", "m", {**card("f", "u"), "defaultInputModes": ["text"]}
    )
    files.card["skills"] = [{"id": "s", "inputModes": ["text/plain"]}]
    plain = Part(raw=b"x", media_type="text/plain")
    image = Part(raw=b"x", media_type="image/png")
    untyped = Part(url="https://f.example/x")
    assert accepted_parts(text_only, [plain, image, untyped]) == [untyped]
    assert accepted_parts(files, [plain, image]) == [plain]
    assert accepted_parts(agent("open"), [plain, image]) == [plain, image]
