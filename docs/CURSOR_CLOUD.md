# Cursor Cloud Agent workflow

This repository is configured so development can continue from Cursor Cloud Agents without a local computer being online.

## What Cursor prepares

Repository-level `.cursor/environment.json` is the source of truth for the Cloud Agent environment. It references `.cursor/Dockerfile`, runs `.cursor/install.sh` during a Cursor Build, and runs `.cursor/start.sh` at the beginning of each agent session.

The resulting environment provides:

- Python 3.12 and `.venv`
- project dependencies including `dev` and `parsing` extras
- Docker and Docker Compose
- PostgreSQL from the repository's existing `docker-compose.yml`
- Qdrant from the repository's existing `docker-compose.yml`
- the normal development database plus the test database
- Alembic migrations applied to the development database

No local desktop or laptop is part of that runtime path.

## Start work from iPhone or web

1. Start a Cursor Cloud Agent for `siinanXD/document-intelligence-mvp`.
2. Give it one Linear issue only.
3. Tell it to inspect the issue, current `main`, `CLAUDE.md`, `AGENTS.md` and `docs/agent-workflow.md` before changing code.
4. Let it work on the Linear issue branch, run the relevant tests, push and open/update the PR.
5. Review the PR from the phone. GitHub CI plus the configured Codex/Copilot review loop remains the merge gate.
6. Merging follows the guarded auto-merge policy in `docs/AUTOMATIONS.md`: eligible low-risk PRs squash auto-merge once CI and verified review findings are clean; high-risk PRs carry `owner-approval-required` and wait for one owner decision.

A useful task prompt is:

```text
Implement <LINEAR-ISSUE> from the latest main.
Read CLAUDE.md, AGENTS.md and docs/agent-workflow.md first.
Stay inside the issue scope and use the Linear branch name.
Use the configured Cursor Cloud environment; do not depend on my local computer.
Run the relevant tests and bash .cursor/check.sh before pushing.
Open or update the PR, report test evidence and known limitations, and do not merge.
```

For an existing PR with findings:

```text
Continue the current issue PR in Cursor Cloud.
Read every unresolved Codex/Copilot finding and verify it against the current code before changing anything.
Apply only valid findings with the smallest safe fix and a regression test where appropriate.
Run bash .cursor/check.sh, push to the same PR, and do not merge.
```

## Verification commands

Cloud startup is automatic, but it is safe and idempotent to rerun:

```bash
bash .cursor/start.sh
```

Run the CI-equivalent backend checks with:

```bash
bash .cursor/check.sh
```

For retrieval changes, run the offline evaluation as well:

```bash
source .venv/bin/activate
python -m app.evaluation
```

## Secrets

The repo-managed Cloud environment intentionally contains no credentials. Normal tests and CI use mocked providers and do not require paid APIs.

When a task explicitly needs an external provider, add the value through Cursor Cloud **Secrets** so it is exposed as an environment variable. Never commit it to this repository.

Typical optional secrets are:

- `OPENAI_API_KEY`
- Langfuse configuration when tracing integration is being tested
- S3-compatible storage credentials for an explicit integration/deployment task

Availability is not authorization: do not use a paid provider merely because a key exists.

## Environment lifecycle

Cursor Builds run the `install` step and persist the resulting disk state. Long-running processes are not persisted, so Docker/PostgreSQL/Qdrant are started in the `start` step for every Cloud Agent session.

The repo-level `.cursor/environment.json` takes precedence over personal/team saved environments for this repository. When its Dockerfile or install setup changes, Cursor creates a new Build; a failed Build does not replace the last successful one.

## Frontend

The cockpit is `web/`. `.cursor/install.sh` runs `npm ci --prefix web` from the
committed lockfile. `.cursor/check.sh` runs frontend lint, typecheck and tests
alongside the backend checks.
