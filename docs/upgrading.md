# Upgrading and compatibility

Three things move independently: **`lemonade-a2a`**, the **`a2a-sdk`** it is built on, and
**Lemonade Server**. This page says how to upgrade any of them and know that it is safe.

## After an upgrade: one command

```bash
pip install -U a2a-sdk lemonade-a2a      # pip's verb is "install -U", there is no "pip update"
lemonade-a2a doctor
```

`doctor` compares what is installed and reachable with what the project **tested**
(`src/lemonade_a2a/spec/compat.json`, generated from CI results) and what each feature **needs**
([features.md](features.md)). Example after pulling an SDK release newer than the ones the manifest lists (a hypothetical 1.2.3 here):

```text
Installed components
  [ ok ] Python version: Python 3.11.7
  [ ok ] lemonade-a2a version: lemonade-a2a 0.1.0
  [warn] a2a-sdk: a2a-sdk 1.2.3 is inside the declared range >=1.2.0,<1.3 but was not tested (tested: 1.2.0, 1.2.1, 1.2.2)
         next: Run `lemonade-a2a doctor --deep` and the TCK, or pin a tested version.
  [ ok ] Feature registry: 49 features
  [ ok ] Configuration: profile local
```

| Exit code | Meaning |
|---|---|
| `0` | Everything checked passed |
| `1` | Warnings only (for example an untested but in-range version) |
| `2` | A failure: a known-broken or out-of-range version, an invalid configuration, a declared capability the adapter does not support |
| `3` | Something could not be checked (for example Lemonade is not running) and nothing failed |

A failure outranks "could not check", which outranks a warning. Add `--strict` to treat warnings as
failures, which makes `doctor` usable as a deployment gate. Useful options: `--deep` runs a tiny
streaming generation against Lemonade; `--adapter-url URL` also checks a running adapter's Agent
Card against the registry; `--sdk-gap` lists what the installed SDK offers that the adapter does not
use; `--json` for tools.

## Pinning, warning or refusing

`LEMONADE_A2A_COMPAT` decides what the adapter itself does at startup, with the same local checks:

| Mode | Behaviour |
|---|---|
| `warn` (default) | Logs each check that did not pass; starts anyway |
| `strict` | Refuses to start if any check did not pass (warnings count) |
| `off` | Does not look |

The result is exported as the `lemonade_a2a_compat_status` metric (0 pass, 1 warn, 2 fail).

## What "supported" means

* **The declared range.** `pyproject.toml` pins `a2a-sdk>=1.2.0,<1.3`. pip will not install an SDK
  outside it, so `pip install -U a2a-sdk` stays on the newest 1.2.x. That is the safe default.
* **Tested versions.** The manifest lists the SDK versions the canary ran green. A version inside the
  declared range but not in that list is *probably* fine and reported as a warning until the canary
  has covered it.
* **Known broken.** A version a canary run failed on is recorded with the reason and reported as a
  failure.

## How the claims are kept true (the canary)

`.github/workflows/canary.yml` runs `scripts/canary.py` weekly, on demand and on pull requests that
touch dependencies or the specification data. Each run builds a clean environment per SDK version and
runs the whole test suite plus `doctor`:

| Variant | What | If it fails |
|---|---|---|
| `lowest` (1.2.0) | The oldest SDK the range allows | **Blocks merging**: the declared range would be a false claim |
| `latest` | The newest SDK the range allows (what `pip install -U` gives) | Opens an issue on the scheduled run |
| `unbounded` | The newest SDK or pre-release with our upper bound removed | Informational: shows what widening the range would break |

A separate job runs the telemetry tests against the newest OpenTelemetry. To widen the SDK range,
change the bound in `pyproject.toml`, let the canary run green on the new version, then regenerate
the manifest: `python scripts/canary.py --sdk 1.2.0 --sdk latest --output canary.json` and
`python scripts/generate_manifest.py --canary canary.json --tck docs/conformance-results.json --lemonade 2026.40.0`.
The manifest is generated, never hand-edited, so it cannot claim more than was run.

## Lemonade Server versions

`doctor` reads the version from `/api/v1/health`. Versions in the manifest's `lemonade.tested` list are
backed by **recorded streams** in `tests/contract/fixtures/lemonade/<version>/`; a test fails if a
tested version has no recordings. To add a release:

```bash
python scripts/record_contract_fixtures.py --model <small model> --reasoning-model <reasoning model>
# review tests/contract/fixtures/lemonade/<version>/*, then regenerate the manifest with --lemonade <version>
```

A Lemonade version newer than the newest tested produces a warning, an older-than-minimum one a failure.

## Versioning policy

`lemonade-a2a` follows [semantic versioning](https://semver.org/) once it reaches 1.0; until then a
minor release (`0.N`) may change behaviour and each such change is listed under *Upgrade notes* in
[CHANGELOG.md](../CHANGELOG.md).

| Surface | Compatible within a minor release | Breaking (needs a minor/major bump and a note) |
|---|---|---|
| Settings and environment variables | Adding one; changing a default only with an upgrade note | Removing or renaming one; making a rule stricter without a profile |
| `lemonade-a2a` commands and JSON output | Adding commands, flags or JSON fields | Removing or renaming them; changing exit-code meaning |
| Feature registry ids | Adding ids | Renaming or removing an id |
| Extension API ([extending.md](extending.md)) | Adding optional methods | Incrementing `API_VERSION` (the previous version keeps working for at least one minor release) |
| Wire behaviour (the A2A protocol) | Anything the TCK and the A2A spec allow | Dropping a supported binding or method |

**Deprecation.** A setting or feature is deprecated (state `deprecated` in the registry, a startup
warning that names the replacement) for at least one minor release before removal. The registry's
`until` field records when.

## Security and privacy of the tools

`doctor`, `capabilities`, `config` and `support-bundle` are local and read-only: they talk only to
the Lemonade and adapter URLs you configure, send nothing to the project and make no calls to PyPI.
`support-bundle` redacts secrets and your home directory and never contains prompts or responses (the
adapter does not record them); read it before attaching it to an issue.
