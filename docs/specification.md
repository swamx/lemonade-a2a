# Compatibility & Capability Specification

**Status: v0.1, implemented** (the draft was accepted into the roadmap and built; this document now describes what exists). The registry it describes is [features.md](features.md); the user-facing workflow is [upgrading.md](upgrading.md); the plugin API is [extending.md](extending.md).

## 1. Goal

Make the adapter **plug and play and verifiable**. After

```bash
pip install -U a2a-sdk lemonade-a2a      # pip's verb is "install -U"
lemonade-a2a doctor
```

the user gets, in seconds, an evidence-backed answer to: *is this combination supported, what features do I have, what changed, and what should I do?* The aim is **control** (pin, warn or refuse; switch features on and off), **flexibility** (swap the backend, auth, task store or telemetry without forking) and **better support** (one redacted bundle that answers a bug report).

### Why it is needed

Four things move independently:

| Component | Moves because | How the project tracks it |
|---|---|---|
| A2A protocol (1.0, later 1.x) | the Linux Foundation spec evolves | the official TCK, pinned in `tck.yml`, two runs enforced in CI |
| `a2a-sdk` (Python) | releases on PyPI | declared range `>=1.2.0,<1.3` plus a **canary matrix** ([canary.yml](../.github/workflows/canary.yml)) |
| Lemonade Server | releases, new backends and endpoints | **recorded streams per version** ([tests/contract](../tests/contract)) and `doctor` probes |
| `lemonade-a2a` | this project | semantic versioning ([upgrading.md](upgrading.md#versioning-policy)) |

## 2. Terms

- **Component**: one of the rows above, plus Python and informational libraries.
- **Feature**: a named capability with a stable id (`a2a.streaming`, `adapter.auth.mtls`).
- **Claim**: "feature F works with component range R", always with **evidence** (a test, a TCK run, a recorded measurement).
- **Verdict**: `pass`, `warn`, `fail` or `unknown`, produced by `doctor` per check.

MUST, SHOULD and MAY are used as in RFC 2119.

## 3. Feature registry

One machine-readable file shipped in the package, [`features.json`](../src/lemonade_a2a/spec/features.json) (JSON Schema alongside), rendered to [features.md](features.md) by `scripts/generate_features_doc.py`. Each feature has:

| Field | Meaning |
|---|---|
| `id` | `<domain>.<name>`; domains: `a2a` (protocol), `adapter` (this package), `lemonade` (backend behaviour relied on), `sdk` (what the A2A SDK offers) |
| `state` | `supported`, `experimental`, `unsupported`, `deprecated`, `observed` (seen, relied on or worked around), `unknown` |
| `since` / `until` | adapter versions |
| `config` | the setting that controls it |
| `toggle` | for features `LEMONADE_A2A_FEATURES` may switch: the setting and its on/off values |
| `active` | how to tell whether it is in effect for a configuration |
| `verified_by` | evidence: `tests/file.py::test_name`, `script:path`, `tck:official`, `interop:clients` |
| `notes` | caveats |

**Rules enforced in CI** (`tests/test_registry.py`): the file matches its schema; ids are well formed; a `supported` feature has evidence; **every piece of evidence exists** (the named test function, script or passing TCK result); every setting a feature names is a real setting; every toggle works; `docs/features.md` is regenerated from the registry (so the two cannot disagree).

## 4. Compatibility manifest

[`compat.json`](../src/lemonade_a2a/spec/compat.json) records what was tested: Python versions, A2A protocol versions, the `a2a-sdk` declared range and tested versions (plus `known_broken` entries), Lemonade tested versions and minimum, and the evidence (canary results, TCK commit). It is **generated** by `scripts/generate_manifest.py` from canary and TCK results, never edited by hand, so it cannot claim more than was run.

## 5. Commands

All are local and read-only and make no network calls except to the Lemonade and adapter URLs you configure. Human-readable by default; `--json` for tools. `lemonade-a2a` with no command, or with only flags, means `serve`.

| Command | Does |
|---|---|
| `doctor` | Cross-validates installed components against the manifest and registry, checks configuration and plugins, probes Lemonade (reachability, version from `/api/v1/health`, the configured model and its context window; `--deep` adds a tiny streaming generation) and, with `--adapter-url`, a running adapter's Agent Card against the registry |
| `doctor --sdk-gap` | What the installed `a2a-sdk` offers that the adapter does not use (gRPC, push notifications, extended card, v0.3 compatibility, ...) |
| `capabilities [--all] [--json]` | The registry resolved for *this* configuration: which features are on, which are available but off and how to switch them |
| `config show \| validate \| schema` | Effective configuration with secrets redacted and the **source of each value** (default, file, env, cli); validation without starting; a JSON Schema of every setting |
| `support-bundle` | One redacted zip: versions, effective config, doctor output, resolved features, manifest, Lemonade info, log tail. Secrets and the home directory are scrubbed; prompts and responses are never recorded |
| `serve` / `version` | Run the adapter; print versions |

### `doctor` checks

| Check | pass | warn | fail |
|---|---|---|---|
| `a2a-sdk` | in the manifest's `tested` list | inside the declared range but untested | `known_broken`, outside the range, or missing |
| Lemonade version | tested | newer than tested, or never tested | below the minimum |
| Python | tested | supported (`requires-python`) but untested | below the minimum |
| Adapter | matches the manifest | manifest generated for another version | |
| Registry | well-formed | | malformed |
| Configuration | valid | an allowed but weak combination (for example `lan` without TLS) | invalid (the command exits) |
| Plugins | installed and built for this extension API | a plugin that failed to load | a plugin for another API version, or a selected plugin that is not installed |
| Configured model | present; context window shown | context window under 4,096 tokens | not in Lemonade's model list |
| Running adapter | card matches the registry | | declares a capability the registry says is unsupported, or an untested protocol version |

Lemonade or the adapter not answering is `unknown`, not `fail`. **Exit code:** any fail → `2`; otherwise any unknown → `3`; otherwise any warn → `1`; otherwise `0`. `--strict` turns warnings into failures.

## 6. Runtime exposure

- **`GET /.well-known/lemonade-a2a/capabilities`**: the registry resolved for the running configuration plus component versions and the compatibility status. **Off by default** (`LEMONADE_A2A_EXPOSE_CAPABILITIES=1`) and **authenticated** like every non-discovery route, because version disclosure helps attackers. It is deliberately not part of the A2A Agent Card.
- **Agent Card extension** `urn:lemonade-a2a:ext:capabilities:v1`, only when the endpoint is enabled, always `required: false`, carrying the endpoint URL and `specVersion`. A plain A2A client ignores it.

## 7. Control

- **`LEMONADE_A2A_COMPAT`** `off` | `warn` (default) | `strict`: the startup gate runs the local checks (no network). `warn` logs each that did not pass; `strict` refuses to start unless every check passes (warnings count). The result is exported as the `lemonade_a2a_compat_status` metric.
- **Feature flags**: `LEMONADE_A2A_FEATURES="+adapter.cancel_on_disconnect,-a2a.streaming"` toggles registry features that declare a `toggle` (see `capabilities`). An unknown or non-toggleable id is a configuration error. Switching a *core* feature off changes both the Agent Card and the behaviour: with `-a2a.streaming` the card says `streaming: false` and the streaming methods answer *unsupported operation* (`UNSUPPORTED_OPERATION`, tested on both bindings), so card and behaviour cannot disagree.
- **Layered configuration**: defaults, then a TOML file (`--config` or `LEMONADE_A2A_CONFIG`, a `[lemonade_a2a]` table of setting names), then environment variables, then command-line flags. Every setting also stays an environment variable, so existing deployments are unchanged.

## 8. Flexibility: extension API

Four extension points, discovered through Python entry points, each a small contract with an `api_version`; fully described in [extending.md](extending.md): `lemonade_a2a.backends`, `lemonade_a2a.task_stores`, `lemonade_a2a.authenticators`, `lemonade_a2a.telemetry`. `doctor` lists installed plugins and their package versions; a plugin for another API version is refused at startup and failed by `doctor`. The built-in implementations use the same seams: the persistent **SQLite task store** (`LEMONADE_A2A_TASK_STORE=sqlite`) is the reference task-store plugin, and the test suite includes a second backend and an authenticator plugin running real requests.

## 9. Upgrade safety

- **Canary matrix** ([canary.yml](../.github/workflows/canary.yml), [scripts/canary.py](../scripts/canary.py)): lowest supported (blocks), latest in range (opens an issue on the scheduled run) and an unbounded pre-release variant (informational), each running the full suite and `doctor` in a clean environment; plus a job against the newest OpenTelemetry.
- **Backend contract tests** ([tests/contract](../tests/contract)): recorded streams per Lemonade version (`scripts/record_contract_fixtures.py`) and hand-authored streams in the shapes of OpenAI, llama.cpp and vLLM servers, replayed against the client and the executor. A Lemonade version in the manifest without recordings fails a test.
- **Package check** (`package` job in [ci.yml](../.github/workflows/ci.yml)): the wheel is built, installed alone into a clean environment with extras, and the commands above are run.
- **Version policy**: [upgrading.md](upgrading.md#versioning-policy).

**Not implemented:** a release gate that refuses to cut a release when evidence is stale, because there is no release pipeline yet (publishing to PyPI needs the owner's explicit approval); merging third-party plugins' own registry entries into `capabilities`.

## 10. Non-goals

- A new protocol or a replacement for the TCK; the TCK stays the arbiter of A2A conformance.
- Telemetry sent to the project: the commands are local. They make no calls to PyPI.
- Hiding incompatibilities: an unsupported combination is reported as such, with the reason.

## 11. Decisions taken (formerly open questions)

1. **Lemonade version**: read from `version` in `/api/v1/health` (present on 2026.40.0); `lemonade --version` is the fallback an operator can use.
2. **Canary against real Lemonade**: the canary uses the mock and the recorded streams, not a real Lemonade, because a real one needs a model and (usually) a GPU. Real-Lemonade evidence stays with `scripts/validate_real_lemonade.py`, the opt-in integration suite and recorded fixtures per release.
3. **Where the registry lives**: in this repository. A native Lemonade implementation (roadmap P3) would claim features by the same ids and run the same contract tests; whether its registry lives upstream is part of that proposal.
4. **PyPI**: not published; the manifest generation and package job are ready for it.
