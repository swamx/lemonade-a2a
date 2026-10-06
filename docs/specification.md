# Compatibility & Capability Specification

**Status: draft v0.1 (design only).** Nothing in this document is implemented yet unless it says so. It defines what the roadmap's *P2 specification* workstream will build, so the scope can be reviewed before code is written.

## 1. Goal

Make the adapter **plug and play and verifiable**. After

```bash
pip install -U a2a-sdk lemonade-a2a      # "pip update" in the request; pip's verb is install -U
lemonade-a2a doctor
```

the user gets, in seconds, an evidence-backed answer to: *is this combination supported, what features do I have, what changed, and what should I do?* The aim is **control** (pin, warn or refuse; turn features on and off), **flexibility** (swap the backend, auth, task store or telemetry without forking) and **better support** (one redacted bundle that answers a bug report).

### Why it is needed

Four things move independently:

| Component | Moves because | Today |
|---|---|---|
| A2A protocol (1.0, later 1.x) | the Linux Foundation spec evolves | tracked by the TCK pin in `tck.yml` |
| `a2a-sdk` (Python) | releases on PyPI | pinned `>=1.2.0,<1.3`; `pip install -U a2a-sdk` therefore stays on 1.2.x. That is a safe default, but the range is a guess, not a tested claim |
| Lemonade Server | releases, new backends and endpoints | tested by hand on 2026.40.0 |
| `lemonade-a2a` | this project | 0.1.0, unreleased |

Compatibility is currently tribal knowledge. This specification turns it into data that tools can check.

## 2. Terms

- **Component**: one of the rows above, plus Python and informational libraries (uvicorn, Starlette, OpenTelemetry).
- **Feature**: a named capability with a stable id, for example `a2a.streaming` or `adapter.auth.mtls`.
- **Claim**: the project's statement "feature F works with component version range R", always with **evidence** (a test, a TCK run, a recorded measurement).
- **Verdict**: `pass`, `warn`, `fail` or `unknown`, produced by `doctor` for each check.

Requirement words (MUST, SHOULD, MAY) are used as in RFC 2119.

## 3. Feature registry

A single machine-readable file shipped inside the package (`lemonade_a2a/spec/features.json`, JSON Schema alongside). Each feature has:

| Field | Meaning |
|---|---|
| `id` | `<domain>.<name>`; domains: `a2a` (protocol), `adapter` (this package), `lemonade` (backend behaviour the adapter relies on), `sdk` (what the A2A SDK offers) |
| `state` | `supported`, `experimental`, `unsupported`, `deprecated`, `observed` (seen but not relied on) |
| `since` / `until` | adapter versions |
| `config` | the setting that controls it, if any |
| `requires` | component ranges needed (for example `lemonade >= 2026.40`) |
| `verified_by` | test ids, TCK requirement ids or script names that prove it |
| `notes` | caveats, with a link to the documentation |

A `supported` feature MUST have at least one `verified_by` entry, and a CI check fails when the registry names a test that does not exist (pytest marker `@pytest.mark.feature("a2a.streaming")` ties tests to ids).

### Seed contents (current state of the code)

| Id | State | Evidence today |
|---|---|---|
| `a2a.protocol.1_0` | supported | TCK 157 passed, 0 failed |
| `a2a.protocol.0_3_compat` | unsupported | compat layer disabled in `server.py` |
| `a2a.binding.jsonrpc`, `a2a.binding.http_json` | supported | TCK + five independent client libraries |
| `a2a.binding.grpc` | unsupported | decision in [conformance.md](conformance.md) |
| `a2a.streaming`, `a2a.task.cancel`, `a2a.task.list`, `a2a.task.subscribe` | supported | TCK + live tests |
| `a2a.push_notifications`, `a2a.extended_agent_card` | unsupported | blocked on the URL policy / no identity-specific card |
| `a2a.parts.text` | supported | |
| `a2a.parts.file`, `a2a.parts.url`, `a2a.parts.data` | unsupported | rejected with `CONTENT_TYPE_NOT_SUPPORTED` (tested) |
| `adapter.auth.api_key`, `adapter.auth.multi_key`, `adapter.auth.mtls` | supported | [security.md](security.md), tests |
| `adapter.ratelimit`, `adapter.profile`, `adapter.tls` | supported | tests |
| `adapter.cancel_on_disconnect` | supported (opt-in) | live test + real-Lemonade proof |
| `adapter.backpressure` | supported | provided by the SDK queue; verified with a stalled client |
| `lemonade.stream.sse` | supported | validator, benchmarks |
| `lemonade.cancel_by_disconnect` | supported | cancellation proof: backend frees in 177 ms |
| `lemonade.reasoning_content` | observed | Gemma-4 streams it; the adapter does not forward it |
| `lemonade.usage_reporting` | unknown | to be probed |
| `sdk.otel_hooks` | observed | SDK emits spans when OpenTelemetry is installed |

## 4. Compatibility manifest

A second shipped file, `lemonade_a2a/spec/compat.json`, records what was tested. Illustration (values are examples):

```json
{
  "schema_version": 1,
  "spec_version": "0.1",
  "adapter": "0.1.0",
  "python": {"tested": ["3.11", "3.12", "3.13"]},
  "a2a_protocol": {"supported": ["1.0"]},
  "a2a_sdk": {
    "declared": ">=1.2.0,<1.3",
    "tested": ["1.2.0", "1.2.1"],
    "status": "supported",
    "evidence": {"tck_commit": "263b9cf", "ci": "workflow run id"}
  },
  "lemonade": {"tested": ["2026.40.0"], "minimum": "2026.40.0"},
  "known_broken": [{"component": "a2a_sdk", "range": "<1.2.0", "reason": "..."}]
}
```

The manifest is **generated** at release time from CI results, not edited by hand, so it cannot claim more than was run.

## 5. Commands

All commands are local, read-only and make no network calls except to the Lemonade and adapter endpoints the user points them at. Output is human-readable by default and stable JSON with `--json`.

| Command | Does |
|---|---|
| `lemonade-a2a doctor` | Cross-validates the installed components against the manifest and registry, probes Lemonade (health, version, models, streaming, the `reasoning_content` behaviour) and, if running, the adapter (Agent Card, declared interfaces and capabilities versus the registry, auth and profile consistency). Ends with a verdict and next steps |
| `lemonade-a2a capabilities` | Prints the feature registry resolved for *this* install and configuration: what is supported here, what is off and why |
| `lemonade-a2a config show \| validate \| schema` | Effective configuration with secrets redacted and the source of each value; validation without starting; JSON Schema of every setting |
| `lemonade-a2a support-bundle` | One redacted archive (versions, effective config, doctor output, recent logs, platform and Lemonade system info; builds on `diagnostics.py`). Never contains prompts, responses or keys |
| `lemonade-a2a doctor --sdk-gap` | Lists what the installed `a2a-sdk` offers that the adapter does not use (new bindings, push, extended card, new request-handler options), so an SDK upgrade shows *new* possibilities and not only breakage |

### `doctor` checks and exit codes

| Check | pass | warn | fail |
|---|---|---|---|
| `a2a-sdk` version | inside `tested` | inside `declared` but untested | in `known_broken` or outside `declared` |
| Lemonade version | inside `tested` | newer than tested | below `minimum` |
| Python | tested | untested but supported by `requires-python` | unsupported |
| Protocol | adapter and card agree on 1.0 | | mismatch |
| Config | valid and consistent with the profile | weak combination | invalid |
| Card vs registry | every declared capability is `supported` | | declares something unsupported |
| Backend probe | streaming works, models listed | optional behaviour missing | unreachable (`unknown`, not `fail`) |

Exit code: `0` all pass, `1` warnings only, `2` any failure, `3` could not check. `--strict` turns warnings into failures, which makes `doctor` usable as a deployment gate.

## 6. Runtime exposure

- **Operational endpoint** `GET /.well-known/lemonade-a2a/capabilities`: the same data as `capabilities --json` plus component versions. Authenticated like every non-discovery route, and off unless enabled (`LEMONADE_A2A_EXPOSE_CAPABILITIES=1`), because version disclosure helps attackers. Kept *out of* the A2A Agent Card, in line with the hardware-neutrality rule in [architecture.md](architecture.md).
- **Optional Agent Card extension** `urn:lemonade-a2a:ext:capabilities:v1`, always `required: false`, carrying only the endpoint URL and `spec_version`, so a smart client can discover the richer description and a plain A2A client is unaffected.

## 7. Control: compatibility modes and feature flags

- `LEMONADE_A2A_COMPAT` = `off` | `warn` (default) | `strict`. At startup the adapter evaluates the same checks as `doctor`. `warn` logs, `strict` refuses to start on a failed check, and `off` skips them. The mode and its result are exported as telemetry (see [observability.md](observability.md)).
- **Feature flags** for opt-in and experimental features by registry id (`LEMONADE_A2A_FEATURES="+adapter.cancel_on_disconnect"`). Core protocol features cannot be silently disabled: turning one off changes what the Agent Card declares and makes the matching methods answer "unsupported operation", so card and behaviour always agree (checked by `doctor`).
- **Layered configuration**: defaults, then a config file (`lemonade-a2a.toml`), then environment variables, then command-line flags. Every setting keeps working as an environment variable, so existing deployments do not change.

## 8. Flexibility: extension API

Named extension points, discovered through Python entry points, each a small `typing.Protocol` with an `api_version`:

| Group | Replaces | First use |
|---|---|---|
| `lemonade_a2a.backends` | The Lemonade client: any OpenAI-compatible server, or a native in-process route | Other local servers; the native Lemonade path in P3 |
| `lemonade_a2a.task_stores` | The in-memory bounded store | Persistent tasks (SQLite) |
| `lemonade_a2a.authenticators` | API-key middleware | Token validation behind a gateway |
| `lemonade_a2a.telemetry` | OpenTelemetry wiring | Custom exporters |

Rules: a plugin declares the registry features it adds or changes; `doctor` lists installed plugins with their versions and flags API-version mismatches; the extension API follows its own semantic version and gets a deprecation period of at least one minor release.

## 9. Upgrade safety (how the claims stay true)

- **Canary matrix** (scheduled CI): lowest supported, latest stable and latest pre-release `a2a-sdk`, each running unit tests, the TCK (official and patched) and `doctor`. A failure on *latest* opens an issue automatically and does not block merges; a failure on *lowest* does.
- **Backend contract tests** (`tests/contract/`): recorded request/response and stream fixtures per Lemonade version, replayed against the client, plus the opt-in real-Lemonade suite. A new Lemonade release is added by recording fixtures.
- **Version policy**: the adapter follows semantic versioning. Widening the `a2a-sdk` range requires a green canary; dropping support for a version is a minor release with a deprecation note.
- **Release gate**: the manifest is generated from the release build's CI run; a release cannot be cut if a `supported` feature has failing or missing evidence.

## 10. Non-goals

- A new protocol or a replacement for the TCK; the TCK stays the arbiter of A2A conformance.
- Telemetry sent to the project: `doctor` and `support-bundle` are local. A "check for newer versions" option may call PyPI only when explicitly requested (`--check-updates`).
- Hiding incompatibilities: an unsupported combination is reported as such, with the reason.

## 11. Open questions

1. Is `lemonade-a2a` published to PyPI, and under what release process? (Blocked on owner approval; the manifest is generated at release, so the pipeline needs it.)
2. How does `doctor` learn the Lemonade version reliably? Candidates: a version field in `/api/v1/health` or `system-info`; otherwise the `lemonade` CLI.
3. Should the canary also run real Lemonade on a Linux runner (CPU model), or only recorded fixtures?
4. Should the registry cover native-Lemonade behaviour in P3, or live in the upstream project?
