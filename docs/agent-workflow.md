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
* Review comments do not authorize blind changes. Verify each finding against the actual code, then apply only the smallest safe fix with a regression test where appropriate. Outdated, duplicate, already-fixed and factually incorrect findings get a factual reply, not a code change.
* A fix stays on the existing issue branch and existing PR. After the push, CI and automatic review run again. At most three automatic repair rounds per failing condition; after that the blocker is documented on the PR and the Linear issue and the loop stops.

## Merge policy: guarded auto-merge

Merging follows the guarded auto-merge policy in `docs/AUTOMATIONS.md`:

* A low-risk PR that passes the full eligibility checklist there (one Linear issue, test evidence for every acceptance criterion, all required checks green on the current head, mergeable on current `main`, no unresolved valid review finding, no secrets, no paid provider calls, no weakened tests) is squash auto-merged without owner approval.
* A PR touching any mandatory human-gate category (authentication/authorization, tenant isolation, secrets, deployment/infrastructure, destructive migrations, deletion/retention semantics, GitHub Actions/permissions, major dependency upgrades, paid live-provider execution, Safety PLC behavior, machine control, protected PLC blocks, ambiguous acceptance criteria, reviewer-classified high risk) is fully prepared, labeled `owner-approval-required`, and stops for one explicit owner decision. The `merge-gate` check fails while that label is present.
* Squash is the only merge method. The Linear issue moves to Done only after the PR is actually merged and `main` CI is green.

## Rules

* Never force-push a branch someone else may have checked out.
* Never commit secrets or a filled `.env`. Rotate anything leaked.
* Never make paid external API calls from tests or CI - mock providers.
* Never skip, disable or delete a test to make CI green.
* Extend the existing CI workflow rather than adding a parallel one. (`merge-gate.yml` is the documented exception: it must react to label events without restarting the test matrix.)
* Deploying and any outward-facing action beyond the guarded auto-merge policy stay with the repository owner.

## CI

`.github/workflows/ci.yml` runs on every push and pull request: it installs the
project with dev extras on Python 3.12, runs `ruff check .`, `ruff format --check .`
and `pytest`. A red CI run is the author's to fix.

For non-draft pull requests, the same workflow also requests GitHub Copilot code
review on open/reopen/ready-for-review and on every new PR commit. The request uses
GitHub's built-in token and the documented `copilot-pull-request-reviewer[bot]`
reviewer; no external API key or paid provider call is added to CI.
