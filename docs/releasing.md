# Releasing

**There is no release pipeline yet and nothing is published to PyPI.** This page is the checklist for the
first release, so the steps are agreed before anyone runs them. Publishing, tagging and creating a GitHub
release all need the repository owner's explicit approval each time.

## Before cutting a version

1. **CI is green on `main`**: tests on Python 3.11 to 3.13, coverage, security checks, the TCK job (official
   and workaround runs) and the package job.
2. **Run the canary locally or from CI** and keep the JSON:
   `python scripts/canary.py --sdk 1.2.0 --sdk latest --tck-dir <a2a-tck> --output canary-results.json`.
   A failure on the lowest or latest version in the declared range blocks the release (the declared range
   would be a false claim); a failure on `unbounded` only says what widening the range would break.
3. **Validate on real Lemonade**: `python scripts/validate_real_lemonade.py` and
   `lemonade-a2a doctor --deep` against the Lemonade version you want to list as tested.
4. **Record fixtures** for a new Lemonade version (`scripts/record_contract_fixtures.py`) and review them.
5. **Regenerate the generated files** and commit them:
   - `python scripts/generate_manifest.py --canary canary-results.json --tck docs/conformance-results.json --lemonade <tested versions>`
   - `python scripts/generate_features_doc.py`
   - the TCK results: `python scripts/run_tck.py ...` (official) and the patched run.
6. **Update `CHANGELOG.md`** (including *Upgrade notes*) and the version in `pyproject.toml` (the
   package reads its own version from the installed metadata, so there is one place to change).

The manifest records the adapter version it was generated for; `lemonade-a2a doctor` warns when the two
differ, so a stale manifest is visible to users.

## Build and check

```bash
python -m build                           # sdist and wheel in dist/
python -m pip install dist/*.whl          # in a clean environment, extras: dist/*.whl[otel,sqlite]
lemonade-a2a doctor --no-lemonade --sdk-gap
```

The `package` job in CI does exactly this on every pull request.

## Publish (owner only)

Use PyPI **trusted publishing** from a GitHub Actions workflow (an OIDC token, no stored API token), with a
protected environment that requires the owner's approval, and publish to TestPyPI first. That workflow
does not exist yet; adding it is part of the owner's decision to publish.

## After the release

- Tag the release commit and create the GitHub release from the changelog entry.
- Keep the canary running weekly; a new `a2a-sdk` release inside the range is picked up automatically as
  "untested but in range" by `doctor` until the next manifest is generated.
