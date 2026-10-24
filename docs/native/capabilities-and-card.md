# Agent Card generation and feature claims

**Status: draft (2026-10-15).** The adapter builds its card from static settings; native can read live state.

## Card contents

The card keeps the adapter's content rules, all enforced by tests there and portable as a golden-file check (`examples/agent-card.json` is already that golden file: `tests/test_agent_card.py` fails when it drifts from the generated card):

- one interface per advertised binding, URLs derived from `a2a.public_url` or the request `Host`;
- `streaming: true` only when the streaming route is enabled; push notifications and the extended card stay
  undeclared (see [conformance.md](../conformance.md#optional-capabilities-re-evaluated-2026-10-06));
- a bearer security scheme **only when `LEMONADE_API_KEY` is enforced**;
- no vendor, accelerator, engine or hardware term in any configuration (identical card whichever backend runs);
- any extension is optional (`required: false`).

## What native can do that the adapter cannot

The adapter has no access to Lemonade's internals, so its card uses fixed text and one generic `local-chat`
skill. Native can read model state in-process (*verified*: `ModelManager::get_supported_models`,
`get_downloaded_models`, `get_model_info`; `Router::get_all_loaded_models`; and MCP already exposes a
`list_models` tool built on the same calls).

| Card element | Proposal | Why |
|---|---|---|
| Skills | One generic text-chat skill in the first slice; modality skills (vision, speech, image generation) only when a downloaded model of that type exists | The architecture rule: never claim a skill because a model *name* suggests it; claim it because a capability of that type is present |
| Input and output modes | `text` only until non-text parts are accepted | Matches the current rejection of file, URL and data parts |
| Model list | **Not** in the card. Models are a server resource and change at runtime; a client that needs them uses the existing models endpoint | Keeps the card stable and cacheable |
| Skill tags | Modality, never a model or backend name | Hardware neutrality |
| Card caching | `Cache-Control` and `ETag` as in the adapter; regenerate when the set of downloaded model *types* changes | A card that changes per request defeats caching |

Open (decision D5): whether the card should be generated per request (always current, a little work per
discovery call) or cached and invalidated by model-manager events. The adapter's cache headers suggest the
second; upstream's threading model decides it.

## Model resolution for a message

A2A messages carry no model name. Resolution follows MCP's precedence (*verified* in `mcp_server.h`):
explicit model in message metadata, then a loaded model of the right type, then a downloaded one, then a clear
failure ("no model available"); a large download is never started implicitly.

## Feature claims

The adapter's registry (`src/lemonade_a2a/spec/features.json`, 49 features, rendered to
[features.md](../features.md)) lists each feature with a state and `verified_by` evidence. Native should claim
features by the **same ids** so that a client, `doctor`-style tooling and the compatibility manifest speak one
vocabulary.

Needed change (decision D7): `verified_by` currently names pytest ids and scripts that only exist for the Python
implementation. The registry needs an `implementation` dimension, for example
`{"python": {...}, "native": {...}}`, with the rule *a feature is claimed by an implementation only if that
implementation has passing evidence*. Evidence for native is the shared black-box suites (TCK, interop clients,
black-box end-to-end, contract fixtures) plus upstream's own C++ and Python integration tests.

| Feature area | Expected native state in the first slice |
|---|---|
| Protocol 1.0, Agent Card, JSON-RPC send and get | claim after the first slice's suites pass |
| Streaming, cancellation, close-before-FAILED | second slice |
| HTTP+JSON binding | third slice |
| Named keys, rate limits, mTLS, exposure profiles | **not applicable** (upstream's key and proxy model) |
| Extension API, persistent store, doctor, config layers | **not applicable** |
| Telemetry instruments | mapped onto upstream telemetry, see [validation-and-migration.md](validation-and-migration.md) |

The registry should record "not applicable" with a reason rather than silently omitting a feature, so the
difference between the two implementations is explicit.
