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
leading bytes all have to agree. Upload size is capped by `MAX_UPLOAD_BYTES`
(default 50 MiB) and enforced while the body streams: an oversized request is
refused with `413` as soon as the limit is crossed, without buffering the rest
of the body. The file SHA-256 used for duplicate detection is computed in that
same pass.

### Searching

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "X-Tenant-Id: <tenant uuid>" \
  -H "content-type: application/json" \
  -d '{"query": "payment terms", "limit": 5}'
```

Optionally narrow to particular documents with `"document_ids": ["..."]`. The
filter can only narrow: every Qdrant query carries the tenant filter, built by
the retrieval service rather than by callers, so naming another tenant's
document matches nothing.

Results carry the score from the index and the text and provenance from
PostgreSQL. Qdrant holds identifiers only - no chunk text - so the collection
can be rebuilt at any time and a breach of it yields ids rather than documents.

### Asking a question

```bash
curl -X POST http://127.0.0.1:8000/ask \
  -H "X-Tenant-Id: <tenant uuid>" -H "content-type: application/json" \
  -d '{"question": "When is payment due?"}'
```

The answer comes back with the source ids it actually used, each resolvable:

```bash
curl http://127.0.0.1:8000/documents/<document id>/sources/<source id> \
  -H "X-Tenant-Id: <tenant uuid>"
```

`has_sufficient_evidence: false` is a successful response, not an error - "the
documents do not say" is a real answer, and the alternative is a confident
guess. Source ids the model names but was never given are dropped, so every id
in a response resolves. When two passages disagree, `conflicting` is true and
both are returned rather than reconciled into one smooth answer.

### Document relations

```bash
curl http://127.0.0.1:8000/documents/<id>/relations -H "X-Tenant-Id: <tenant uuid>"
```

Each relation carries the signals that produced it:

```json
[{"relation_type": "possible_version", "score": 0.94,
  "reason": {"signals": ["document_vector", "shared_entities"],
             "similarity": 0.94, "threshold": 0.92,
             "shared_organizations": ["Acme"]},
  "target": {"filename": "contract-v1.pdf", "title": "Service Agreement"}}]
```

The rules run most-certain-first and the first match wins: identical bytes, then
identical normalized text, then high similarity *with* shared parties, then a
shared identifier, then shared parties, then similarity alone. A document that
matches none of them gets no relation - saying "related" about everything would
make the feature noise.

Thresholds are configuration (`RELATION_*`), not constants scattered through
the code, so tuning them is one place and every one is visible.

### Lexical search

```bash
curl -X POST http://127.0.0.1:8000/search \
  -H "X-Tenant-Id: <tenant uuid>" -H "content-type: application/json" \
  -d '{"query": "INV-2024-0042", "mode": "lexical"}'
```

Semantic search finds passages that *mean* the same thing; lexical search finds
passages that *contain* the literal text - an invoice number, a clause
reference, a remembered phrase. It is answered by PostgreSQL alone, so it keeps
working when the embedding provider or the vector store does not.

### Running the worker

```bash
pip install -e ".[dev,parsing]"     # the parsing extra brings Docling
python -m app.worker
```

The worker claims queued ingestion jobs from PostgreSQL, parses each document
with Docling, normalises the text, computes `content_hash`, stores the parsed
representation for later reindexing, and writes chunks with their provenance.
It then embeds those chunks and indexes them in Qdrant, in the same
transaction, so a document reads `ready` only once it is actually searchable.
It runs from the same project as the API - one codebase, two entry points, and
it refuses to start without an embedding provider rather than quietly marking
documents ready that answer nothing.

**Docling needs its models.** The PDF pipeline downloads a layout model on
first use, and the `HybridChunker` downloads a tokenizer. Left to itself that
happens inside whichever request is the first PDF, costs hundreds of megabytes,
and repeats on every fresh container. A deployment should pre-fetch them at
image build time and point `DOCLING_ARTIFACTS_PATH` at the result. OCR is off
by default (`DOCLING_DO_OCR`): it is the expensive path, pulls further models,
and scanned documents are out of scope for the MVP.

### Health endpoints

- `GET /health` — liveness. Answers `200` as long as the process serves
  requests, so a brief database outage does not trigger a restart.
- `GET /health/ready` — readiness. Probes PostgreSQL and Qdrant concurrently
  under a short timeout and returns `503` with `status: degraded` when one is
  unreachable. It reports up or down only; endpoints and credentials stay in
  the logs.

### Tests

```bash
pytest --ignore=tests/test_docling_parser.py    # the fast suite
pytest tests/test_docling_parser.py             # the real parser; needs the
                                                # `parsing` extra
pytest                                          # database tests skip if
                                                # PostgreSQL is unreachable
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/document_intelligence_test \
  REQUIRE_DB=1 pytest                           # how CI runs it: a missing
                                                # database fails instead of skipping
```

Create the test database once with
`createdb document_intelligence_test`, or let `docker compose up -d` provide
PostgreSQL and create it there. The schema is built by running the real
migrations, so every test run also exercises the migration path.

### Retrieval evaluation

```bash
python -m app.evaluation                        # hashing embeddings, no paid calls
python -m app.evaluation --mode lexical
python -m app.evaluation --embeddings live      # opt-in; never ordinary CI
```

See [`docs/EVALUATION.md`](docs/EVALUATION.md). The golden corpus is synthetic
and contains no customer documents. Cross-tenant leakage must be zero.

### Generation controls and tracing

Every LLM call has an explicit timeout, retry budget, `max_tokens` cap and
`temperature=0`. Completions return a provider-neutral envelope (tokens, cost
when priced, prompt name/version, latency, trace/request id). Tracing is
optional and fail-open; Langfuse is `pip install -e ".[observability]"` and is
not required for requests to work. See [`docs/GENERATION.md`](docs/GENERATION.md).

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
  worker.py     the ingestion worker: python -m app.worker
migrations/     Alembic environment and revisions
tests/          pytest suite; every external call is mocked
docs/           workflow, privacy, evaluation and generation notes
```

Layering: `api -> services -> providers`. Routes never reach Qdrant, object
storage or an AI provider directly, and vendor SDKs are imported only inside
provider implementations.

## Contributing

Agents and humans follow the same workflow: one issue per branch and pull request,
small commits, green checks before pushing. See
[`docs/agent-workflow.md`](docs/agent-workflow.md) for the details and
[`CLAUDE.md`](CLAUDE.md) for architecture, tenant-isolation, provider and privacy rules.
