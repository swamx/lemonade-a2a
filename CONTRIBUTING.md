# Contributing

Thanks for helping make local Lemonade inference interoperable through A2A.

## Priorities

1. standards correctness;
2. interoperability;
3. safe local defaults;
4. minimal adapter overhead;
5. upstream-friendly design;
6. experimental routing/scheduling only after the core is stable.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

CI runs exactly these (Python 3.11-3.13), plus a mock-Lemonade black-box job.

## Test layers

| Layer | Command | Needs |
|---|---|---|
| Unit / in-process | `pytest` | nothing |
| Black-box vs mock Lemonade | `python scripts/blackbox_e2e.py` | mock (`tests/mock_lemonade.py`) and adapter running |
| Smoke conformance | `python scripts/check_a2a_conformance.py` | adapter running |
| Independent clients | `node interop/js_client.mjs`, `python interop/inspector_validate.py --inspector <clone>` | adapter running, see [docs/interoperability.md](docs/interoperability.md) |
| Official A2A TCK | `python scripts/run_tck.py --tck-dir <a2a-tck>` | a2a-tck clone with its own venv, see [docs/conformance.md](docs/conformance.md) |
| Real Lemonade, pytest | `LEMONADE_INTEGRATION=1 pytest tests/integration -m integration` (skipped otherwise; failures write logs and environment to `integration-diagnostics/`) | real Lemonade + model |
| Real Lemonade, one command | `python scripts/validate_real_lemonade.py` (starts the adapter, runs validator, pytest suite and a benchmark) | real Lemonade + model |
| Client libraries | `interop/go`, `interop/dotnet`, `interop/java` (commands in [docs/interoperability.md](docs/interoperability.md)) | adapter running, Go / .NET 8 / JDK 21 + Maven |
| Real Lemonade | `python scripts/real_lemonade_e2e.py` | real Lemonade + model, see [docs/real-lemonade-validation.md](docs/real-lemonade-validation.md) |
| Benchmarks | `python benchmarks/benchmark_evidence.py` | real Lemonade + adapter, see [docs/benchmarks.md](docs/benchmarks.md) |

## Pull requests

Nothing is pushed to `main` directly. Every change is a pull request that needs the owner's approval and green checks: ruff and format, tests on Python 3.11-3.13, coverage of at least 95% (`pytest --cov`), the mock black-box E2E, bandit, pip-audit, secret scan, dependency review and CodeQL. AI agents may open PRs but do not approve or merge them. Details: [docs/governance.md](docs/governance.md).

Run the main gates locally:

```bash
pip install -e '.[dev]'
ruff check . && ruff format --check . && pytest --cov
bandit -c pyproject.toml -r src -ll && pip-audit .
```

Keep protocol changes small and document the relevant A2A semantic. New capabilities should include tests. Do not advertise unsupported Agent Card skills.

Changes to the A2A wire behavior should include interoperability evidence where possible.

## Architecture rule

Do not put model inference into the A2A adapter. Lemonade owns model execution. This project owns protocol translation, task lifecycle and related policy.

## Scope boundary

Do not add a parallel reasoning or model-routing framework. Lemonade owns model selection,
backend routing and inference; A2A changes here should focus on interoperability and
must preserve standards-compliant behavior.

## Security

Do not open a public issue containing working exploit details for a suspected vulnerability. Contact the repository owner privately until a formal security policy is published.
