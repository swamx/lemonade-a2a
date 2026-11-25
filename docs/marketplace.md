# Agent marketplaces: agents that talk to other agents

Give a Lemonade agent the link or domain name of an agent marketplace and it can consult
any agent listed there over A2A. Nothing else is needed: the marketplace is read, every
listed Agent Card is fetched and validated, and the local model decides per request
which of those agents should help.

Status: **experimental**, new in 0.2.0 (`adapter.marketplace*`, `adapter.conversation_memory`
and `adapter.file_parts` in the [feature registry](features.md)). This page is the reference;
for a step-by-step walk-through on one machine, follow the
[multi-agent pathway](pathways/multi-agent.md).

## Quick start

```bash
pip install -e .                # from a clone of this repository (not on PyPI yet)
export LEMONADE_MODEL=your-model  # with Lemonade Server running

# What does the marketplace offer?
lemonade-a2a agents list agents.example.com

# Talk to one of its agents directly
lemonade-a2a agents ask weather "Will it rain in Austin tomorrow?" -m agents.example.com

# Run your own agent that consults the marketplace when it helps
lemonade-a2a serve --name "Trip Planner" --port 9101 --marketplace agents.example.com
```

Several agents on one machine are several `serve` processes with their own `--port` and
`--name`; the card advertises `http://localhost:<port>` unless you set `--public-url`.

Each running agent is itself an A2A agent with an Agent Card at
`/.well-known/agent-card.json`, so it can be listed in a marketplace in turn.

## What counts as a marketplace

There is no single standard listing format yet, so the reader accepts the common shapes.

**Where it looks.** A bare domain (`agents.example.com`) is first looked up in DNS (see
[DNS SRV discovery](#dns-srv-discovery)); then, or when it has no records, it is probed at,
in order:

1. `/.well-known/agent-registry.json`
2. `/.well-known/agents.json`
3. `/agents.json`
4. `/.well-known/agent-card.json` (a domain that serves one agent is a marketplace of one)

A full link (`https://agents.example.com/api/v1/agents?limit=100`) is fetched as is, and
`dns:agents.example.com` uses DNS records only. Several marketplaces can be given,
comma-separated.

### DNS SRV discovery

Like other services, a marketplace can be published in DNS, so the domain you give does not
have to host it:

```text
_a2a-registry._tcp.example.com. 300 IN SRV 10 50 443 registry.example.net.
_a2a-registry._tcp.example.com. 300 IN TXT "path=/api/v1/agents"
_a2a._tcp.example.com.          300 IN SRV 10 50 8443 agent.example.net.
```

`_a2a-registry._tcp` points at hosts serving a listing, `_a2a._tcp` at a host serving one
agent's card. Targets are tried by priority, then weight (RFC 2782); a `.` target means "not
offered". The optional TXT record on the same name gives `path=...` (DNS-SD style
`key=value`); without it the well-known paths above are used. The scheme is always `https`
(`http` only for a local target with `--marketplace-allow-local`), and the port is omitted
when it is 443. Each target URL passes the same URL and SSRF policy as a typed link, and the
marketplace API key is sent only to the domain you named, never to a host a DNS answer
named (plain DNS can be spoofed).

**What the document may look like.** A JSON array, or an object that holds the array under
`agents`, `items`, `results`, `data`, `entries`, `agentCards`, `cards` or `records` (one level
of nesting such as `{"data": {"items": [...]}}` is fine). Each entry may be:

| Entry | Example |
|---|---|
| A full Agent Card (1.x `supportedInterfaces` or 0.3 `url`) | `{"name": "Weather", "supportedInterfaces": [...], "skills": [...]}` |
| An object embedding a card | `{"id": "weather", "agentCard": {...}}` (also `card`, `agent_card`) |
| An object pointing at a card | `{"id": "weather", "agentCardUrl": "https://w.example/card.json"}` (also `cardUrl`, `card_url`, `wellKnownURI`) |
| An object with the agent's base URL | `{"id": "weather", "url": "https://w.example"}` (also `baseUrl`, `endpoint`); the card is read from `/.well-known/agent-card.json` under it |
| A plain URL string | `"https://w.example"` or `"/agents/weather/.well-known/agent-card.json"` |

Relative URLs resolve against the listing. The entry's `id` (or `agentId`, `slug`) becomes
the agent id; otherwise a slug of the card's name is used. Ids are unique within a session
(`weather`, `weather-2`, ...). Entries that cannot be read are skipped and reported
(`agents list` prints them as `[warn]`), so one broken entry does not hide the rest.

A minimal marketplace is a static file:

```json
{
  "agents": [
    {"id": "weather", "url": "https://weather.example"},
    {"id": "flights", "agentCardUrl": "https://travel.example/agents/flights.json"}
  ]
}
```

served as `https://agents.example.com/.well-known/agent-registry.json`.

## How a request is handled

1. The agent list is loaded on first use and refreshed every
   `LEMONADE_A2A_MARKETPLACE_REFRESH_SECONDS` (a failed refresh keeps the previous list).
2. **Planning.** The local model gets a short, non-streamed prompt with the agents' ids, names,
   descriptions and skills (and the last few turns of the conversation) and answers with
   `{"calls": [{"agent": "<id>", "message": "..."}, ...]}` or `{"calls": []}`. A message that
   starts with `@agent-a @agent-b ...` skips this step and asks those agents.
3. **Delegation.** The agents of one round are asked at the same time through the official A2A
   SDK client (JSON-RPC or HTTP+JSON, 1.x or 0.3, whichever the card offers). A task that is
   still running is polled until it finishes or `LEMONADE_A2A_DELEGATION_TIMEOUT_SECONDS` pass.
   While budget is left (`LEMONADE_A2A_MAX_DELEGATIONS`, default 3 calls per task), the model
   sees the replies and may plan another round, for example asking a weather agent about the
   city a flights agent proposed. Set it to `1` for one call per task.
4. **Answer.** With `LEMONADE_A2A_MARKETPLACE_REPLY=compose` (default) the local model writes
   the answer with the replies as context; with `relay` the replies are returned as they are
   (one per agent, under its name). If an agent fails, the local model answers and says so: a
   remote failure never fails the task by itself.

### Conversations (multi-turn)

Messages with the same `contextId` are one conversation:

- the last `LEMONADE_A2A_CONVERSATION_TURNS` turns (default 10) are given to the model, so
  follow-ups like "and tomorrow?" work; memory is per authenticated caller, in memory only,
  bounded and dropped after an hour idle;
- each marketplace agent keeps its own remote `contextId` for the conversation, so a later
  request continues the same remote conversation;
- when an agent answers `input-required`, the local task becomes `input-required` with that
  question (`Weather Agent needs more information: Which city?`). Reply in the same context,
  with the task id, and the reply goes to the agent's waiting task:

```python
async with A2AClient("http://localhost:9101", allow_local=True) as planner:
    first = await planner.ask("@weather forecast for tomorrow")
    if first.state == "TASK_STATE_INPUT_REQUIRED":
        print(first.status_text)
        answer = await planner.ask("Austin", context_id=first.context_id, task_id=first.task_id)
```

### Files

With `LEMONADE_A2A_FILES=1` an agent accepts file and data parts (the card lists the media
types): text files and JSON go to the model, images too with `LEMONADE_A2A_FILE_IMAGES=1`
(vision model needed), URL parts as references. The parts are forwarded with each request to
marketplace agents whose card accepts their media type (others get the text only), and file
parts the agents return are added to the task as a
`delegated-files` artifact (`AgentReply.files` on the client side).

The delegation runs inside the A2A task, so the task deadline, `CancelTask` and the
concurrency cap apply to it. With `LEMONADE_A2A_REASONING=artifact`, a line naming the agent
consulted appears in the `lemonade-reasoning` artifact.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LEMONADE_A2A_MARKETPLACE` | empty (off) | Marketplace links or bare domain names, comma-separated |
| `LEMONADE_A2A_MARKETPLACE_ALLOW_LOCAL` | `0` | Allow `http` and private/loopback addresses: development or a LAN marketplace only; refused under profile `external` |
| `LEMONADE_A2A_MARKETPLACE_ALLOWED_HOSTS` | empty | If set, only these hosts may be contacted (listing, cards and agents) |
| `LEMONADE_A2A_MARKETPLACE_API_KEY` | empty | Bearer token sent to the marketplace hosts only, never to the agents they list |
| `LEMONADE_A2A_MARKETPLACE_REFRESH_SECONDS` | `300` | How long the agent list is cached |
| `LEMONADE_A2A_MARKETPLACE_REPLY` | `compose` | `compose` (local model answers using the reply) or `relay` (remote reply verbatim) |
| `LEMONADE_A2A_DELEGATION_TIMEOUT_SECONDS` | `120` | How long to wait for one remote agent; keep it below `LEMONADE_A2A_MAX_TASK_SECONDS` |
| `LEMONADE_A2A_MAX_DELEGATIONS` | `3` | Remote agent calls per task, across planning rounds |
| `LEMONADE_A2A_CONVERSATION_TURNS` | `10` | Earlier turns of a `contextId` given to the model; `0` turns memory off |
| `LEMONADE_A2A_FILES` / `LEMONADE_A2A_FILE_IMAGES` | `0` / `0` | Accept file and data parts / also images |
| `LEMONADE_A2A_MAX_FILE_BYTES` | `524288` | Largest inline file part |

`serve` takes `--marketplace`, `--marketplace-allow-local`, `--marketplace-reply`, `--name`,
`--description`, `--model`, `--public-url` and `--lemonade-url`.

## From Python

```python
from lemonade_a2a.discovery.marketplace import Marketplace

market = Marketplace("agents.example.com")
for agent in await market.agents():
    print(agent.id, agent.name, [s["name"] for s in agent.skills])

reply = await market.ask("weather", "Will it rain in Austin tomorrow?")
print(reply.state, reply.text)
```

`Marketplace` implements the discovery `resolve(alias)` interface, so it can replace
`StaticDiscovery`. `market.ask(...)` and `A2AClient.ask(...)` take `context_id`, `task_id`
and `parts` to continue conversations and send files. `A2AClient.ask(text)` is the one-call helper for an agent you already
know (`async with A2AClient("https://weather.example") as c: await c.ask("...")`). Failures
raise the typed errors of `lemonade_a2a.client.errors`.

## Security

Listings, cards and replies are third-party input.

- **SSRF.** Every request (listing, cards, A2A calls) goes through the guarded transport that
  resolves DNS once and refuses private, loopback, link-local and metadata addresses. Redirects
  are refused. A card whose advertised endpoint fails that policy is dropped.
- **Bounds.** Listings up to 4 MiB and 200 agents, cards up to 1 MiB, card fetches 8 at a time;
  names, descriptions and skills are trimmed to single lines of bounded length.
- **Credentials.** The marketplace key goes to the marketplace hosts you named only (not to
  SRV targets). The agent's own
  inbound API keys are never forwarded. Agents that require their own credentials are not
  supported yet.
- **Prompt injection.** Agent descriptions and replies are fenced and labelled as data in the
  prompts, and a reply cannot close its own fence, but a model can still be swayed by what
  they say. Use marketplaces you trust, set `LEMONADE_A2A_MARKETPLACE_ALLOWED_HOSTS` to pin
  them, and prefer `compose` with a capable model. The adapter never takes actions on a remote
  agent's say-so: it only sends one text message per task.
- **Privacy.** A delegated request leaves the machine. Leave the marketplace unset for
  strictly local use.

## Not yet supported

Signed-card verification, DNSSEC validation, per-agent credentials, streaming a remote
agent's partial output through, and fetching file URLs inside the adapter. While one
agent waits for input, replies from other agents asked in the same round are not kept.
