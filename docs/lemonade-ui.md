# Chat with your agents in Lemonade's UI

Lemonade's web app is a chat client for Lemonade's own models; it does not speak A2A. It can
also show models from **cloud providers**: OpenAI-compatible servers that Lemonade lists in its
model picker and forwards chats to. Each lemonade-a2a agent serves an OpenAI-compatible chat
surface, so it can be added as such a provider. You then pick the agent in Lemonade's chat like
any model, and it answers as it does over A2A: delegating to marketplace agents, remembering the
conversation and showing what it does as "thinking".

Status: **experimental**, new in 0.2.0 (`adapter.openai_api` in the [feature registry](features.md)).
Verified with Lemonade Server 2026.40.0 on Windows. It was checked through the same HTTP calls the
web app makes (`/api/v1/install`, `/api/v1/models`, `/api/v1/chat/completions`, streaming and
not), not by clicking through the browser UI.

```text
Lemonade web app ──► Lemonade Server ──(cloud provider)──► Trip Planner agent :9100
                          ▲                                   │ A2A
                          │                                   ▼
                          └─────── model calls ◄──────  marketplace agents
```

The loop back into Lemonade is fine: Lemonade forwards the chat without holding up its own
model, so the agent's calls to the local model run as usual.

## Add an agent

1. Start Lemonade with a model, and your agents (the [multi-agent pathway](pathways/multi-agent.md),
   steps 1-5, sets up a Trip Planner with two specialists). Use a 4B-class model or larger for an
   agent that plans delegations.
2. Register the agent with Lemonade:

   ```bash
   lemonade-a2a lemonade-ui add --name "Trip Planner" --port 9100
   ```

   ```text
   added 'trip-planner' to Lemonade as provider 'tripplanner'; pick it in the model list of Lemonade's chat
   ```

   Use the same `--name` and `--port` (or `--public-url`) as the `serve` command, or the same
   environment and config file, so the command knows which agent to register. The agent must be
   running: Lemonade asks it for its model while registering.
3. Open Lemonade's chat (`lemonade run <model>` opens it, or browse to `http://localhost:13305`)
   and choose **trip-planner** in the model list. It is grouped under its provider.
4. Chat. Start a message with `@weather` to ask a specific marketplace agent, or just ask and
   let the agent decide. "Consulting marketplace agent ..." lines appear in the model's thinking.

**From the web app instead of the command line:** Lemonade's settings have a form for adding a
cloud provider. Enter a name (for example `tripplanner`), the base URL
`http://localhost:9100/v1`, any API key (for example `local`; Lemonade only lists a
provider's models when it has a key), and accept the warning about plain `http://` (the agent is
on the same machine).

Add each agent you want to chat with the same way. Specialists can be added too.

**Remove:** `lemonade-a2a lemonade-ui remove --name "Trip Planner"`.

**After restarting Lemonade:** Lemonade keeps the key it was given in memory only, so run
`lemonade-ui add` again, or set `LEMONADE_TRIPPLANNER_API_KEY` (the provider name in upper case)
in Lemonade's environment.

## What works in the chat

| In Lemonade's chat | What the agent does |
|---|---|
| Streaming answers | Streamed as they are generated |
| Thinking / reasoning panel | The model's reasoning and the "Consulting marketplace agent ..." notes |
| Follow-up questions | The chat sends the whole conversation each time; that is what the model sees |
| A marketplace agent asks a question back | The question is the answer; your next message goes back to that agent's waiting task |
| Images (vision models) | Accepted inline when the agent runs with `LEMONADE_A2A_FILES=1` and `LEMONADE_A2A_FILE_IMAGES=1`; remote image URLs are refused, never fetched |
| Temperature, max tokens and other sampling settings | Ignored: the agent decides how its model is used |
| Tool calling | Not offered: the agent answers with text only |
| Files returned by marketplace agents | Named in a note at the end of the answer (a chat completion cannot carry files) |

## The chat surface

Every agent serves it unless `LEMONADE_A2A_OPENAI_API=0`:

| Route | Does |
|---|---|
| `GET /v1/models` | One model: the agent, with an id made from its name (`Trip Planner` → `trip-planner`) |
| `POST /v1/chat/completions` | OpenAI chat completions, `stream` true or false; also accepts Lemonade's `<provider>.<id>` model names |

It runs the same executor as A2A, so the per-task deadline, the concurrency cap (HTTP 429 when
full), authentication, rate limits and the request-size limit apply unchanged. Errors reach the
client as short, sanitized messages (`Lemonade backend is unavailable.`), as an OpenAI error
object or, mid-stream, as an `error` event followed by `[DONE]`.

Any OpenAI-compatible client works the same way, not only Lemonade:

```bash
curl http://localhost:9100/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "@weather Rain in Lisbon on Saturday?"}]}'
```

## Security

- The surface is protected exactly like the A2A routes. Under the default `local` profile both
  are unauthenticated and on loopback only; under `lan` and `external` both need an API key, and
  `lemonade-ui add` gives Lemonade the agent's key (`--api-key` to choose one when there are
  several). Lemonade then holds that key; give it a key of its own with `LEMONADE_A2A_API_KEYS`,
  for example `lemonade:...`, so its conversations are separate from other callers'.
- Over a network, use `https://` for the agent: Lemonade sends the key in plain text over
  `http://` (it warns, and `lemonade-ui add` only allows `http://` because that is the common
  same-machine case).
- To keep an agent A2A-only, set `LEMONADE_A2A_OPENAI_API=0`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `found no model at http://.../v1` | The agent is not running, `LEMONADE_A2A_OPENAI_API=0`, or `--port` / `--public-url` do not match the running agent |
| `Lemonade is not reachable` | Lemonade is not running, or it is not at `LEMONADE_BASE_URL`; pass `--lemonade http://host:13305` |
| The agent is gone from the model list after restarting Lemonade | See "After restarting Lemonade" above |
| HTTP 401 in Lemonade's chat | The agent requires an API key and Lemonade has a different one: run `lemonade-ui add --api-key <key>` |
| HTTP 429 | The agent is at `LEMONADE_A2A_MAX_CONCURRENT_TASKS`; retry, or raise it |
| `<agent_reply>` tags in answers, or the wrong agent asked | The agent's model is too small for planning ([pathway troubleshooting](pathways/multi-agent.md#troubleshooting)) |
