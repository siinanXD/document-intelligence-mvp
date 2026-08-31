# Branch and PR workflow for autonomous agents

## One issue, one branch, one PR

1. Pick a single Linear issue. Do not bundle unrelated issues into one branch.
2. Branch from the latest `main`:
   ```bash
   git fetch origin main
   git checkout -B <branch-name> origin/main
   ```
   Use the branch name Linear suggests on the issue when one exists.
3. Implement the issue scope - nothing more. Out-of-scope findings become new issues.
4. Commit in small, logical steps with descriptive messages.
5. Run the checks that CI runs, locally, before pushing:
   ```bash
   ruff check .
   pytest
   ```
6. Push with upstream tracking and open a PR:
   ```bash
   git push -u origin <branch-name>
   ```

## Pull request contents

Fill in `.github/pull_request_template.md`:

* what changed and why, linked to the Linear issue,
* test evidence - the commands run and their result,
* known limitations and anything deliberately left out.

## Review loop

* Codex review is configured outside the repository and starts automatically when a PR is opened for review. Use `@codex review` when an explicit fresh pass is wanted on the current head.
* The CI workflow requests GitHub Copilot code review for every non-draft PR when it is opened, reopened, marked ready, or receives a new push. This gives each fix commit a fresh independent review pass without relying on repository rulesets.
* Copilot review follows `.github/copilot-instructions.md` and should focus on concrete correctness, security, privacy, tenant-isolation and regression findings.
* Review comments do not authorize blind changes. Verify each finding against the actual code, then apply only the smallest safe fix with a regression test where appropriate.
* A fix stays on the existing issue branch and existing PR. After the push, CI and automatic review run again.
* Do not auto-merge. Final merge remains an explicit repository-owner decision after CI and review findings are clean.

## Rules

* Never force-push a branch someone else may have checked out.
* Never commit secrets or a filled `.env`. Rotate anything leaked.
* Never make paid external API calls from tests or CI - mock providers.
* Never skip, disable or delete a test to make CI green.
* Extend the existing CI workflow rather than adding a parallel one.
* Merging, deploying and any outward-facing action stay with the repository owner.

## CI

`.github/workflows/ci.yml` runs on every push and pull request: it installs the
project with dev extras on Python 3.12, runs `ruff check .`, `ruff format --check .`
and `pytest`. A red CI run is the author's to fix.

For non-draft pull requests, the same workflow also requests GitHub Copilot code
review on open/reopen/ready-for-review and on every new PR commit. The request uses
GitHub's built-in token and the documented `copilot-pull-request-reviewer[bot]`
reviewer; no external API key or paid provider call is added to CI.
