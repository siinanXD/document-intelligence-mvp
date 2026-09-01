# Pipeline cockpit

SIN-77 adds a small Next.js App Router UI in `web/`. It calls the existing
FastAPI API. It does not reimplement ingestion, search or answering.

## What it shows

- documents: upload, list, duplicate notice, processing status
- pipeline: `uploaded → parsed → chunked → embedded → indexed → ready`
- search: semantic or lexical, optional document filter, provenance
- ask: grounded answer, evidence/conflict flags, clickable citations
- source detail: stored passage, page and section
- decisions: parser/provider/model, retrieval mode, chunks considered, prompt
  version, latency and cost when the backend supplies them
- evaluation: checked-in retrieval/generation gate status

Failures are classified as configuration, provider, database, vector store or
document processing. The UI never holds OpenAI, S3, PostgreSQL or Qdrant
secrets. `X-Tenant-Id` is a local identification header; production auth is a
later issue.

## Five-minute demo

1. Start PostgreSQL and Qdrant: `docker compose up -d`
2. Apply schema: `alembic upgrade head`
3. API: `uvicorn app.main:app --reload`
4. Worker: `python -m app.worker`
5. UI: `cd web && npm install && npm run dev`
6. Open http://127.0.0.1:3000, create the `demo` tenant, upload a file, open
   it, wait until Ready, then search and ask. Click a citation to the passage.

The local API allows browser origins `http://127.0.0.1:3000` and
`http://localhost:3000` when `ENVIRONMENT=local`. Override with `CORS_ORIGINS`.
The UI talks to `NEXT_PUBLIC_API_BASE_URL` (default `http://127.0.0.1:8000`).
In production the public prefix is `/backend` and a Next.js route handler
proxies that to the private API (`API_UPSTREAM_URL`, read at runtime). See
[`docs/DEPLOYMENT.md`](DEPLOYMENT.md).

## Checks

```bash
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web test
```
