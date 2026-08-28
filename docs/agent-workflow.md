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
