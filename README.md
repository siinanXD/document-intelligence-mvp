# document-intelligence-mvp

A multi-tenant document intelligence service. Documents are uploaded, parsed into a
normalized representation, chunked with provenance, embedded, indexed for semantic
search, and answered over with grounded citations.

The core platform is in place: FastAPI, PostgreSQL and Qdrant wiring, Alembic
migrations and the embedding, LLM and storage provider interfaces. Ingestion,
indexing and Q&A land in the following milestones.

## Requirements

- Python 3.12
- Docker (for the local PostgreSQL and Qdrant services)

## Quickstart

```bash
git clone https://github.com/siinanXD/document-intelligence-mvp.git
cd document-intelligence-mvp

python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env        # fill in locally; never commit it

docker compose up -d        # PostgreSQL on :5432, Qdrant on :6333
alembic upgrade head        # create the schema
uvicorn app.main:app --reload
```

The API is then available on http://127.0.0.1:8000:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","version":"0.1.0","environment":"local"}

curl http://127.0.0.1:8000/health/ready
# {"status":"ready",...,"checks":{"database":"up","vector_store":"up"}}
```

Interactive docs: http://127.0.0.1:8000/docs

`docker compose down` stops the services; `docker compose down -v` also drops
their volumes.

### Uploading a document

```bash
curl -X POST http://127.0.0.1:8000/documents \
  -H "X-Tenant-Id: <tenant uuid>" \
  -F "file=@contract.pdf;type=application/pdf"
# 201 {"document": {..., "status": "queued"}, "duplicate": false}

# the same bytes again: nothing is created, nothing is re-queued
# 200 {"document": {...}, "duplicate": true}

curl http://127.0.0.1:8000/documents -H "X-Tenant-Id: <tenant uuid>"
curl http://127.0.0.1:8000/documents/<id> -H "X-Tenant-Id: <tenant uuid>"
```

Every request carries `X-Tenant-Id`. That header is identification, not
authentication - nothing issues credentials yet, and real authentication is
part of the hardening work. Supported uploads are PDF, DOCX, PPTX, XLSX, HTML,
Markdown and plain text; the extension, the declared content type and the
leading bytes all have to agree.

### Health endpoints

- `GET /health` — liveness. Answers `200` as long as the process serves
  requests, so a brief database outage does not trigger a restart.
- `GET /health/ready` — readiness. Probes PostgreSQL and Qdrant concurrently
  under a short timeout and returns `503` with `status: degraded` when one is
  unreachable. It reports up or down only; endpoints and credentials stay in
  the logs.

### Tests

```bash
pytest                                          # unit tests; database tests skip
                                                # if PostgreSQL is unreachable
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/document_intelligence_test \
  REQUIRE_DB=1 pytest                           # how CI runs it: a missing
                                                # database fails instead of skipping
```

Create the test database once with
`createdb document_intelligence_test`, or let `docker compose up -d` provide
PostgreSQL and create it there. The schema is built by running the real
migrations, so every test run also exercises the migration path.

### Migrations

```bash
alembic upgrade head                            # apply
alembic downgrade -1                            # roll back one revision
alembic revision --autogenerate -m "add x"      # new revision
alembic upgrade head --sql                      # render SQL without a database
```

## Development

```bash
ruff check .        # lint
ruff format .       # format
pytest              # tests
```

`ruff check .`, `ruff format --check .` and `pytest` run in CI on every push and pull
request via [`.github/workflows/ci.yml`](.github/workflows/ci.yml), on Python 3.12.
Tests mock every external provider - CI never makes a paid API call.

## Configuration

All configuration comes from the environment and is read through
`app/core/settings.py`. [`.env.example`](.env.example) lists the variable names.
`.env` is git-ignored and must never be committed.

## Project layout

```
app/
  main.py       FastAPI application factory
  api/          HTTP routes
  core/         settings, database engine, Qdrant client
  providers/    embedding, LLM and storage interfaces plus implementations
  models.py     ORM models: the durable source of truth
  services/     business logic; the only layer that touches Qdrant
migrations/     Alembic environment and revisions
tests/          pytest suite; every external call is mocked
docs/           workflow and design notes
```

Layering: `api -> services -> providers`. Routes never reach Qdrant, object
storage or an AI provider directly, and vendor SDKs are imported only inside
provider implementations.

## Contributing

Agents and humans follow the same workflow: one issue per branch and pull request,
small commits, green checks before pushing. See
[`docs/agent-workflow.md`](docs/agent-workflow.md) for the details and
[`CLAUDE.md`](CLAUDE.md) for architecture, tenant-isolation, provider and privacy rules.
