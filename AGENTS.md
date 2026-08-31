# AGENTS.md

Repository instructions for Cursor Cloud Agents and other autonomous coding agents.

## Read these first

The existing repository rules remain authoritative:

1. `CLAUDE.md` — architecture, tenant isolation, providers, migrations, privacy and secrets.
2. `docs/agent-workflow.md` — one Linear issue per branch/PR, tests, review loop and merge rules.

This file adds Cloud-specific operating instructions; it does not replace those rules.

## Cursor Cloud specific instructions

This repository is configured so a Cloud Agent can work without any local computer being online.

- `.cursor/environment.json` defines the Cloud Agent build and startup lifecycle.
- `.cursor/Dockerfile` supplies Python 3.12 and Docker/Compose support.
- `.cursor/install.sh` creates `.venv` and installs `.[dev,parsing]` during Cursor Builds.
- `.cursor/start.sh` starts Docker, PostgreSQL and Qdrant, prepares the test database and applies Alembic migrations.
- `.cursor/check.sh` runs the CI-equivalent lint, format, backend and parser checks.

At the start of a task, verify the environment rather than assuming it:

```bash
source .venv/bin/activate
docker info
docker compose ps
curl -fsS http://127.0.0.1:6333/collections >/dev/null
```

If services are not ready, run:

```bash
bash .cursor/start.sh
```

Before pushing a code change, run:

```bash
bash .cursor/check.sh
```

For retrieval changes, also run the offline evaluation when relevant:

```bash
source .venv/bin/activate
python -m app.evaluation
```

Do not claim a live-provider result unless an explicitly approved live-provider run was actually performed.

## Secrets and external services

Normal development, tests and CI must work without paid provider credentials.

Never write secrets into `.cursor/environment.json`, shell scripts, `.env.example`, documentation, tests or committed `.env` files. Use Cursor Cloud **Secrets** for credentials that are explicitly needed for a task.

Examples of optional runtime secrets include:

- `OPENAI_API_KEY` for explicitly approved live provider/evaluation work.
- Langfuse credentials when tracing is intentionally being tested.
- S3-compatible storage credentials for an explicit integration/deployment task.

A secret being available does not authorize using it. Do not make paid/external calls unless the issue or user request requires them.

## Branch and PR behavior

- Start from the latest `main` unless continuing an existing issue PR.
- Use the Linear issue branch name when available.
- Keep one Linear issue per branch and PR.
- Do not force-push shared branches.
- Open a PR only after relevant checks pass.
- Fix only verified review findings; do not blindly apply reviewer suggestions.
- Keep fixes on the same issue branch/PR and rerun checks after every fix.
- Do not merge or deploy unless the repository owner explicitly requests it.

## Frontend note

The repository is backend-only today. When SIN-77 adds the Next.js app, update the Cloud environment install step to install the committed frontend lockfile dependencies. Do not add a parallel frontend environment or duplicate backend business logic.
