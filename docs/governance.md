# Governance: how changes reach `main`

Goal: **only the repository owner can approve and merge, nothing reaches `main` without a pull request and green automated checks, and AI agents (Copilot, Claude, others) can propose changes but cannot approve or merge them.**

## What is enforced where

| Layer | Mechanism | Enforces |
|---|---|---|
| GitHub ruleset ([`.github/rulesets/main-protection.json`](../.github/rulesets/main-protection.json)) | Applies to the default branch, **no bypass actors** | No direct pushes, no force-push, no deletion; a PR is required; 1 approval **from a code owner**; the approver cannot be the last pusher; stale approvals are dismissed on new pushes; review threads must be resolved; branch must be up to date; required checks must pass; CodeQL alerts at high severity block |
| [`.github/CODEOWNERS`](../.github/CODEOWNERS) | `* @swamx` | Only the owner's review satisfies the "code owner" requirement, so a review by Copilot, a bot, or any other account never counts |
| CI workflows (`.github/workflows/`) | Required status checks | Lint and format, tests on 3.11-3.13, coverage at or above 95%, black-box E2E, bandit, pip-audit, secret scan, dependency review, CodeQL (Python, JavaScript, Actions) |
| [`.claude/settings.json`](../.claude/settings.json) | Claude Code permission rules | This agent is **denied** `gh pr merge`, `gh pr review`, merge/review API calls, pushes to `main`, and force-pushes; changing rulesets, releases and tags require a prompt |
| Dependabot, secret scanning with push protection, [SECURITY.md](../SECURITY.md) | Repository settings and policy | Weekly dependency/action update PRs; private vulnerability reporting |

## The one thing GitHub cannot do: tell an agent from you

GitHub identifies **accounts and tokens, not whether a human or an AI is typing**. If an agent runs with *your* token (as Claude Code does when it uses your `gh` login), then to GitHub it *is* you:

- you cannot approve a PR that your own token authored (GitHub forbids self-approval), and
- anything you are allowed to do, the agent holding your token is allowed to do (merge, approve, or bypass).

So no ruleset alone can give "I approve, the agent cannot" while both share one identity. Choose one of:

1. **Recommended: give AI agents their own identity.** Create a separate GitHub account (for example `swamx-ai`), add it to the repo as a collaborator with the **Write** role (not Admin/Maintain), and give the agent a fine-grained token for that account. Write access can push branches and open PRs, but with this ruleset (no bypass actors, code-owner approval required) it **cannot merge or approve**. You, a different account, approve and merge in the GitHub UI. This is the only setup where GitHub itself enforces the rule.
2. **Interim, harness-level only:** keep using your token and rely on `.claude/settings.json`. Claude Code will refuse to merge, approve or force-push, but this is a client-side guard for that one tool, not a GitHub guarantee, and a differently configured agent with your token could still do it. With this option PRs authored under your token cannot be approved by you (GitHub forbids self-approval), and the ruleset has no bypass, so in practice you would have to switch the ruleset off to merge (see *Emergency access*). That is why option 1 is strongly preferred.
3. **Third-party agents:** do not install Copilot coding agent or other GitHub Apps with write/admin permissions you do not intend; Copilot's PRs and reviews cannot satisfy a code-owner requirement for `@swamx`, and they have no bypass.

## Emergency access

The ruleset has no bypass actors by design. If you must override it (for example, a broken check blocks an urgent fix), temporarily set the ruleset to `disabled` or `evaluate` in **Settings → Rules**, merge, and re-enable. That is deliberate friction, and it also means an agent cannot do this silently if it lacks admin on the account it uses (option 1).

## Applying the ruleset

The JSON in `.github/rulesets/` is importable (Settings → Rules → Rulesets → New → Import) or can be applied with the API:

```bash
# replace the existing "Default" ruleset (id from `gh api repos/OWNER/REPO/rulesets`)
gh api -X PUT repos/OWNER/REPO/rulesets/<id> --input .github/rulesets/main-protection.json
```

Required check names are the job names in the workflows; renaming a job means updating the JSON. Merge the workflow changes **before** enabling required checks, or the first PRs will wait for checks that do not exist yet.

## Notes on specific rules

- **Coverage** is enforced by the required `coverage` job (`fail_under = 95` in `pyproject.toml`) rather than GitHub's separate coverage rule, so it works without extra services.
- **Code quality** is enforced by ruff lint and format checks in the required `test` jobs.
- **Merge methods** are merge or squash; rebase is disabled to keep PR history reviewable.
- A sole owner can approve any PR authored by another account. If you later add maintainers, raise the required approvals.
