# Pathway: multi-agent systems with a marketplace

**Goal:** run several Lemonade agents on one machine that find each other through an agent
marketplace and delegate work to one another. Then add multi-turn conversations, files and
DNS discovery, and finish with a checklist for exposing the setup beyond your machine.

**You need:** Python 3.11 or newer, Git, and either a running Lemonade Server with a model
loaded or about two minutes to start the mock server (step 1). Everything stays on your
machine until step 8.

**What you build:**

```text
                         you (CLI or Python)
                                │ A2A
                                ▼
                     Trip Planner  :9100   ── reads ──►  marketplace listing  :8800
                       │        │                        (a static JSON file)
              A2A      │        │      A2A
                       ▼        ▼
          Weather Agent :9101   Travel Agent :9102
                       │        │
                       └────────┴──► Lemonade (OpenAI-compatible API) :13305
```

Each box is one `lemonade-a2a serve` process. All of them use the same Lemonade server, so
there is still only one model runtime. The marketplace is just a file listing the agents.

Commands are written for a POSIX shell. On Windows PowerShell, activate the virtual
environment with `.venv\Scripts\activate` and set variables as `$env:NAME = "value"`.

---

## Step 1. Install and check

```bash
git clone https://github.com/swamx/lemonade-a2a.git
cd lemonade-a2a
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
lemonade-a2a version               # lemonade-a2a 0.2.0
```

lemonade-a2a is not on PyPI yet, so it is installed from source.

Start Lemonade Server the way its own documentation describes, with a model loaded. It serves
an OpenAI-compatible API at `http://localhost:13305/v1`. **No hardware or no model yet?**
Start the mock server from the repository's tests instead. It answers every prompt with
`MOCK_LEMONADE_OK`:

```bash
python -m uvicorn tests.mock_lemonade:app --host 127.0.0.1 --port 13305
```

In a new terminal (activate the virtual environment in each one):

```bash
lemonade-a2a doctor
```

**Checkpoint:** the verdict is `PASS` or `WARN`, and the Lemonade section shows the server's
version. A `FAIL` names the problem and the next step to take.

## Step 2. Serve your first agent

An agent is one `serve` process with its own name, description and port:

```bash
lemonade-a2a serve --port 9101 --name "Weather Agent" \
  --description "Weather forecasts for any city"
```

Its Agent Card, which other agents read to discover it, is now public:

```bash
curl http://localhost:9101/.well-known/agent-card.json
```

Send it a message. A host that serves a single Agent Card counts as a marketplace of one, so
`agents ask` can reach the agent directly. Its id is a slug of its name:

```bash
lemonade-a2a agents ask weather-agent "Will it rain in Lisbon tomorrow?" \
  -m localhost:9101 --allow-local
```

**Checkpoint:** you get the model's answer (`MOCK_LEMONADE_OK` with the mock).
`--allow-local` permits `http` and loopback addresses. Every outbound request refuses them by
default, so a remote listing cannot point your agents at your own network.

## Step 3. Add a second specialist

In another terminal:

```bash
lemonade-a2a serve --port 9102 --name "Travel Agent" \
  --description "Flights, trains and hotels between European cities"
```

Each agent advertises `http://localhost:<port>` in its card. Pass `--public-url` when it
should advertise something else, such as a LAN address or a TLS proxy.

## Step 4. Publish a marketplace

A marketplace is any URL that lists agents. The simplest is a static file at a well-known path:

```bash
mkdir -p market/.well-known
cat > market/.well-known/agent-registry.json <<'EOF'
{
  "agents": [
    {"id": "weather", "url": "http://localhost:9101"},
    {"id": "travel", "url": "http://localhost:9102"}
  ]
}
EOF
python -m http.server 8800 --bind 127.0.0.1 --directory market
```

Then, in another terminal:

```bash
lemonade-a2a agents list localhost:8800 --allow-local
```

**Checkpoint:** both agents are listed with the ids from the file:

```text
weather  Weather Agent
    Weather forecasts for any city
    - Local AI: Language-model inference executed by Lemonade on local hardware.
    http://localhost:9101
travel  Travel Agent
    ...
```

Given only `localhost:8800`, the reader tried `/.well-known/agent-registry.json` first. Entries
can also be full Agent Cards or links to cards, and the file can live anywhere if you give its
full URL. See [what counts as a marketplace](../marketplace.md#what-counts-as-a-marketplace).

## Step 5. Run an agent that delegates

The coordinator is an ordinary agent with `--marketplace`:

```bash
lemonade-a2a serve --port 9100 --name "Trip Planner" \
  --description "Plans trips by asking specialist agents" \
  --marketplace localhost:8800 --marketplace-allow-local
```

Ask it something. A message that starts with `@<agent-id>` sends the request to that agent
directly, and several mentions ask several agents at the same time:

```bash
lemonade-a2a agents ask trip-planner "@weather Will it rain in Lisbon on Saturday?" \
  -m localhost:9100 --allow-local
lemonade-a2a agents ask trip-planner "@weather @travel Weekend in Lisbon from Madrid" \
  -m localhost:9100 --allow-local
```

Without a mention, the Trip Planner's own model decides which agents to ask. It can ask
several of them, and with budget left it can ask more in a second round that uses the first
answers:

```bash
lemonade-a2a agents ask trip-planner "Plan a rainy-day-proof weekend in Lisbon" \
  -m localhost:9100 --allow-local
```

**Checkpoint:** the Trip Planner's terminal logs `delegating to marketplace agent weather`.
The specialists' terminals show the requests arriving.

Things to know:

- By default the Trip Planner writes the final answer from the specialists' replies
  (`compose`). Add `--marketplace-reply relay` to return their replies as they are, which suits
  small models.
- Planning needs a model that can reply with a small JSON object. The mock cannot, so with the
  mock an unmentioned question is answered locally. Use `@mentions` or a capable model.
- Up to `LEMONADE_A2A_MAX_DELEGATIONS` (default 3) remote calls are made per task. Each
  planning round is one extra short call to the local model.
- If a specialist is down, the Trip Planner still answers and says which agent could not be
  reached.

## Step 6. Hold a conversation

Messages that share a `contextId` form one conversation, and the agent remembers the last 10
turns. From Python (save this as `talk.py` and run it with the virtual environment active):

```python
import asyncio

from lemonade_a2a.client import A2AClient


async def main() -> None:
    async with A2AClient("http://localhost:9100", allow_local=True) as planner:
        first = await planner.ask("Plan a weekend in Lisbon.")
        print(first.state, first.text)
        follow_up = await planner.ask("Make it cheaper.", context_id=first.context_id)
        print(follow_up.state, follow_up.text)


asyncio.run(main())
```

When a specialist needs more information, it answers `input-required`. The Trip Planner then
pauses its own task with that question, and your answer goes back to the specialist's waiting
task:

```python
reply = await planner.ask("@weather Forecast for Saturday")
if reply.state == "TASK_STATE_INPUT_REQUIRED":
    print(reply.status_text)  # e.g. "Weather Agent needs more information: Which city?"
    reply = await planner.ask("Lisbon", context_id=reply.context_id, task_id=reply.task_id)
```

**Checkpoint:** the follow-up's `context_id` equals the first one's. Memory is per caller, held
in memory only, and cleared after an hour idle; `LEMONADE_A2A_CONVERSATION_TURNS=0` turns it
off.

## Step 7. Send files

Files are off by default. Restart the Trip Planner with them on:

```bash
LEMONADE_A2A_FILES=1 lemonade-a2a serve --port 9100 --name "Trip Planner" \
  --marketplace localhost:8800 --marketplace-allow-local
```

Its card now lists the accepted file types (`text/plain`, `application/json`, and so on). Send
a file:

```python
from a2a.types import Part

notes = Part(
    raw=b"Budget: 300 EUR. No early flights.", media_type="text/plain", filename="notes.txt"
)
reply = await planner.ask("Use my notes for the plan.", parts=[notes])
```

Text files and JSON are given to the model as quoted data. Images are passed too when you also
set `LEMONADE_A2A_FILE_IMAGES=1` (this needs a vision model). The file is forwarded to
specialists whose card accepts its type. Files they send back arrive in `reply.files`. File
URLs are passed on as references and never downloaded by the adapter.

## Step 8. Publish the marketplace in DNS

For a real domain, publish the listing over HTTPS and advertise it with an SRV record, as
other services do:

```text
_a2a-registry._tcp.example.com. 300 IN SRV 10 50 443 registry.example.net.
_a2a-registry._tcp.example.com. 300 IN TXT "path=/agents.json"
```

```bash
lemonade-a2a agents list example.com          # SRV first, then the well-known paths
lemonade-a2a agents list dns:example.com      # SRV records only
```

`_a2a._tcp.<domain>` points at a single agent instead. DNS targets pass the same safety checks
as typed links, so an SRV record cannot point at a private address. Use real, public names
here: `localhost` setups skip SRV. Details: [DNS SRV discovery](../marketplace.md#dns-srv-discovery).

## Step 9. Before you go beyond one machine

| Check | How |
|---|---|
| Marketplace over HTTPS, no local access | Drop `--marketplace-allow-local`. It is refused under profile `external`. |
| Only the hosts you trust | `LEMONADE_A2A_MARKETPLACE_ALLOWED_HOSTS=registry.example.net,weather.example.net` |
| The coordinator is protected | `LEMONADE_A2A_PROFILE=lan` or `external` with `LEMONADE_A2A_API_KEYS`, TLS and a rate limit ([security.md](../security.md)) |
| Specialists can be reached | A coordinator cannot yet present credentials to other agents, so specialists it consults must not require auth. Keep them on a trusted network or behind a gateway that allows the coordinator. |
| Time limits fit | `LEMONADE_A2A_DELEGATION_TIMEOUT_SECONDS` (default 120) below `LEMONADE_A2A_MAX_TASK_SECONDS` (default 600) |
| Privacy is acceptable | Delegated requests and forwarded files leave the machine, and conversation memory holds prompts in RAM |
| Third-party text is handled with care | Listings and replies are quoted as data in prompts, but a model can still be swayed. Prefer trusted marketplaces and a capable model ([threats](../marketplace.md#security)) |
| You can see what happens | Log events `marketplace.problem`, `delegation.start` and `delegation.error` (`LEMONADE_A2A_LOG_FORMAT=json`) |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Non-public IP addresses are not allowed` / `Private or local agent URLs are not allowed` | A loopback or LAN address without `--allow-local` (CLI) or `--marketplace-allow-local` (serve) |
| `no agent listing found at ...` | None of the well-known paths answered with a listing. Give the full URL of the listing file, and check that it is valid JSON. |
| `ConnectError` for an agent in the listing | The agent is not running, or its card advertises an address it does not listen on (`--public-url`) |
| `Media type ... is not supported` | The receiving agent was started without `LEMONADE_A2A_FILES=1`, or the type is not in its card |
| The coordinator never delegates | Its model did not produce the planning JSON. Use `@mentions`, a larger model, or `relay` mode. |
| `profile 'local' listens on loopback only` | You set `--host 0.0.0.0`. Pick `--profile lan` with API keys (step 9). |

## Where next

- [Agent marketplaces](../marketplace.md): every listing shape, setting and security property
- [Security model](../security.md) and [exposure profiles](../security.md#exposure-profiles)
- [Observability](../observability.md): traces, metrics and logs
- [Upgrading](../upgrading.md#from-01-to-02): what changed in 0.2.0
