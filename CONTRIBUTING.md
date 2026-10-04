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
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
ruff check .
```

## Pull requests

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
