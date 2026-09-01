# Production deployment (Railway EU)

SIN-69 deploys the MVP as separate Railway services in **EU West (Amsterdam)**
(`europe-west4-drams3a`). This file is the runbook. It names variables, not
values. Do not commit secrets or a filled `.env`.

Creating, linking and promoting Railway resources is an owner action. The
repository supplies Dockerfiles, config-as-code and smoke commands so that
action is reproducible and non-destructive.

## Services

| Service | Public? | Image / Dockerfile | Config-as-code |
| --- | --- | --- | --- |
| web | yes (HTTPS) | `deploy/Dockerfile.web` | `deploy/railway/web.json` |
| api | no (private DNS) | `deploy/Dockerfile.api` | `deploy/railway/api.json` |
| worker | no | `deploy/Dockerfile.worker` | `deploy/railway/worker.json` |
| postgres | no | Railway PostgreSQL plugin | plugin volume |
| qdrant | no | `deploy/Dockerfile.qdrant` | `deploy/railway/qdrant.json` |
| storage | n/a | Railway S3-compatible bucket | bucket credentials |

Private DNS is `<service>.railway.internal`. The browser talks only to **web**.
Web serves `/backend/*` with a **runtime** route handler that reads
`API_UPSTREAM_URL` and proxies to the private API. That upstream host must
never be assigned to `NEXT_PUBLIC_*` (those values are inlined at build time).

Qdrant must keep a volume at `/qdrant/storage` so a redeploy does not wipe
vectors. Postgres persistence comes with the plugin.

## First-time setup

1. Create a Railway project in EU West. Do not import this as a single
   Nixpacks service; add one service per row above, all from this GitHub repo.
2. For each GitHub-backed service, set **Config-as-code path** to the JSON in
   the table. Railway will use the matching Dockerfile and region.
3. Add the PostgreSQL plugin in the same project. Leave it private.
4. On **qdrant**, add a volume with mount path `/qdrant/storage`.
5. Create a Railway Storage Bucket in EU West. Record the S3 endpoint, bucket
   name and access keys in the dashboard only.
6. Generate a public domain on **web** only. Do not generate public domains for
   api, worker, postgres or qdrant unless you are deliberately debugging.
7. Set variables (names only; values stay in Railway):

### Shared by api and worker

- `ENVIRONMENT=production`
- `DATABASE_URL` (reference the plugin URL; `postgres://` is rewritten to asyncpg)
- `QDRANT_URL=http://qdrant.railway.internal:6333`
- `QDRANT_API_KEY` if you enable one on Qdrant
- `STORAGE_BACKEND=s3`
- `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`
- `OPENAI_API_KEY`
- `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_VERSION`
- `LLM_PROVIDER`, `LLM_MODEL`
- `LOG_LEVEL=INFO`
- `TRACING_CAPTURE_CONTENT` unset or `false`

`PORT` is injected. Do not publish `OPENAI_API_KEY` or S3 keys to the web
service.

### web only

- `NEXT_PUBLIC_API_BASE_URL=/backend`
- `API_UPSTREAM_URL=http://api.railway.internal:8000`

Rebuild web after changing any `NEXT_PUBLIC_*` value; it is inlined at build.

## Authentication

`X-Tenant-Id` remains **identification**, not authentication. Production still
trusts that header. `/dev/tenants` 404s when `ENVIRONMENT=production`. The
public surface is the web origin; the API is private. A dedicated credential
flow is a later hardening issue. Do not treat the tenant header as a secret in
logs.

## Migrations

The API image runs `alembic upgrade head` before serving (pre-deploy and start).
The worker does not migrate. `alembic upgrade head` on an empty database
reproduces the full schema. Check the API deploy log for Alembic output, then
`/health/ready` for `database=up`.

## Health, logs, restart

```bash
# From the public web origin (proxied) or, if you temporarily exposed the API:
export SMOKE_BASE_URL=https://<web-domain>/backend
bash scripts/production_smoke.sh
```

The smoke script prints status and dependency up/down. It does not print
questions, answers, passages, filenames or request bodies.

Railway logs: service → HTTP Logs / Deploy Logs. Restart: **Restart** on the
service. A Qdrant restart must keep the volume; if vectors are empty after a
volume mistake, reindex from Postgres rather than re-uploading.

## Rollback

1. In Railway, open the service → Deployments → redeploy the previous
   successful deployment.
2. Roll **api** and **worker** together if the release included a migration
   that the old image cannot read. Prefer forward-fix migrations; only run
   `alembic downgrade` when the revision has a working downgrade and you have
   a DB backup.
3. Web can roll back independently (static + proxy).
4. Do not delete the Qdrant volume or the Postgres plugin to "reset" a bad
   deploy.

## OpenAI and the EU region

Customer-data services (Postgres, Qdrant, object storage, api, worker, web)
target EU West. OpenAI is still an **external processor**: embeddings and
grounded-answer prompts leave Railway. EU placement of our services does not
by itself make the product GDPR compliant. See `docs/PRIVACY.md`.
