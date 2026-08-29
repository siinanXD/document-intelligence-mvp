# CLAUDE.md

Guidance for coding agents working in this repository.

## What this project is

A multi-tenant document intelligence service: documents are uploaded, parsed into a
normalized representation, chunked with provenance, embedded, indexed for semantic
search, and answered over with grounded citations.

## Architecture

```
app/
  main.py          FastAPI application factory
  api/             HTTP routes only - thin, no business logic
  core/            settings, database engine, Qdrant client
  models.py        ORM models; every tenant-owned row carries tenant_id
  providers/       external systems behind interfaces (embeddings, LLM, storage)
  services/        business logic (ingestion, retrieval, profiling)
  worker.py        ingestion worker; same project as the API
migrations/        Alembic environment and revisions
tests/             pytest suite; external calls are always mocked
```

Layering rule: `api -> services -> providers`. Routes never talk to Qdrant, object
storage or an AI provider directly.

## Commands

```bash
uv venv && source .venv/bin/activate      # or: python3.12 -m venv .venv
uv pip install -e ".[dev]"                # or: pip install -e ".[dev]"

docker compose up -d                      # PostgreSQL :5432, Qdrant :6333
alembic upgrade head                      # apply migrations
uvicorn app.main:app --reload             # run the API on :8000
ruff check .                              # lint
ruff format .                             # format
pytest                                    # tests (database tests need PostgreSQL)
```

## Tenant isolation

* Every persisted row and every vector payload carries `tenant_id`.
* Vector payloads carry identifiers only - never chunk text or filenames. Text
  is read back from PostgreSQL, which stays the source of truth.
* Every query filters by `tenant_id` - repository functions take it as an explicit
  argument, never read it from ambient state.
* Cross-tenant access is a bug, not a permission decision. Each feature that reads
  data ships with a negative test proving another tenant cannot see it.
* Deletion is tenant-scoped and covers object storage, Postgres rows and vector
  points together.
* Uniqueness is scoped to a tenant, never global: two tenants uploading the same
  bytes are not duplicates of each other.

## Provider abstraction

* `EmbeddingProvider`, `LLMProvider` and the storage interface are the only places
  that may import a vendor SDK.
* Provider choice comes from settings through `app/providers/registry.py`; a
  call site asks for a capability and never names a vendor.
* A model must never invent: absent information stays null or empty, and a
  citation the model was not given is dropped rather than returned.
* Implementations take an injected client so tests can supply a fake.
* Embedding provider, model and version are persisted with indexed data so a
  provider change can be detected and reindexed rather than silently mixed.
* Tests mock every external call. CI must never make a paid API call.

## Migrations

* Schema changes go through Alembic; hand-edited SQL against a live database is not
  a migration.
* One logical change per revision, with a working `downgrade` where feasible.
* `alembic upgrade head` on an empty database must reproduce the full schema.
* The database URL comes from `app/core/settings.py`, never from `alembic.ini`.

## Logging and privacy

* Log identifiers (`tenant_id`, `document_id`, `job_id`), never document content,
  extracted entities, filenames of customer data, prompts or completions.
* Never log credentials or full request bodies.
* Never log a question, a retrieved passage, a prompt or a model answer.
* Uploaded bytes and filenames of customer data never reach a log line.
* Errors are logged with context, not with the payload that caused them.
* A parser's own exception may quote the document it failed on. Convert it to a
  message carrying the type only, and never chain the original.

## Secrets

* Never commit secrets. `.env` is git-ignored; `.env.example` lists variable names only.
* Credentials are read from the environment through `app/core/settings.py`.
* If a secret is ever committed, treat it as leaked: rotate it, do not just amend.

## Working agreement for agents

* One Linear issue per branch and pull request.
* Small, logical commits with descriptive messages.
* `ruff check .` and `pytest` pass before pushing.
* Never force-push a shared branch.
* Extend existing workflows and configuration instead of duplicating them.
* See `docs/agent-workflow.md` for the branch and PR workflow.
