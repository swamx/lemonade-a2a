# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a suspected vulnerability.

Use GitHub's private reporting: **Security → Report a vulnerability** on this repository. Include the affected version or commit, what an attacker can do, and a minimal reproduction. You should get an acknowledgement within a few days; this is a community project maintained on a best-effort basis.

## Supported versions

The project is pre-alpha. Only the latest commit on `main` receives fixes.

## Scope notes

- The adapter is designed to run next to a local Lemonade server. Exposing it beyond loopback requires the API key and TLS settings described in [docs/security.md](docs/security.md).
- Findings about third-party components (Lemonade, the A2A SDK, uvicorn) are best reported to those projects; we will help route them.

## Automated checks

Every pull request runs CodeQL, bandit, pip-audit, secret scanning and dependency review (see `.github/workflows/`). Dependabot proposes dependency and action updates weekly.
