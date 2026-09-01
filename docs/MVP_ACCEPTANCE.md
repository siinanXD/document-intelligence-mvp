# MVP acceptance

SIN-70 proves the document-intelligence slice before calling the MVP done.
Every numbered item is either an automated test (no paid providers) or an
explicit command. Live Railway checks that need a deployed project are
[SIN-97](https://linear.app/sinan-kahraman/issue/SIN-97/verify-live-railway-production-smoke-and-private-services-after-first),
not hidden.

`X-Tenant-Id` is identification, not authentication.
[SIN-98](https://linear.app/sinan-kahraman/issue/SIN-98/replace-x-tenant-id-identification-with-production-authentication)
tracks a real credential flow.

Do not log questions, answers, passages, filenames of customer data, prompts,
completions or secrets while running any of these checks.

## Fixture set

Use at least:

| File | Role |
| --- | --- |
| PDF contract | primary document |
| Modified / versioned PDF | similar bytes, same parties |
| Related DOCX | same case / identifier, different body |
| Unrelated `.txt` or `.md` | must not be strongly linked |

The automated scenario in `tests/test_mvp_acceptance.py` builds those files
with valid magic bytes and a fake parser. A local demo can use any real PDF /
DOCX / text files of the same shape; the worker then needs Docling
(`pip install -e ".[dev,parsing]"`).

## Checklist

| # | Item | Automated | Manual command |
| --- | --- | --- | --- |
| 1 | All files process to `ready` | `test_uploaded_files_process_to_ready` | After the worker runs: `GET /documents/{id}/pipeline` shows `current_stage: ready` |
| 2 | Exact duplicate upload is detected | `test_exact_duplicate_upload_is_detected` | Re-upload the same bytes: `200` and `"duplicate": true` |
| 3 | Normalized content duplicate is testable | `test_normalized_content_duplicate_is_linked` | Different bytes, same text after parse → `content_duplicate` on `GET /documents/{id}/relations` |
| 4 | Similar / versioned documents are linked | `test_versioned_pdf_is_linked_and_unrelated_is_not` | Versioned PDF → `possible_version`; related DOCX → `same_case` |
| 5 | Unrelated document is not strongly linked | same test | Unrelated file → empty relations list |
| 6 | Semantic search within one document | `test_semantic_search_within_one_document_and_across_tenant` | `POST /search` with `"document_ids": ["…"]` |
| 7 | Tenant-wide search across documents | same test | `POST /search` without `document_ids` |
| 8 | `/ask` answers with resolvable citations | `test_ask_returns_resolvable_citations_and_surfaces_conflict` | `POST /ask` then `GET /documents/{id}/sources/{source_id}` |
| 9 | Source endpoint returns page / section / provenance | same test | Source body has `page_number`, `section_title`, `text` |
| 10 | Conflicting evidence is surfaced | same test | `"conflicting": true` and both passages returned |
| 11 | Deletion removes storage, Postgres-derived data and Qdrant | `test_deletion_removes_storage_rows_and_search_hits` | `DELETE /documents/{id}` then 404 and empty search |
| 12 | Reindex recreates vectors without re-upload | `test_reindex_recreates_vectors_without_reupload` | `POST /documents/{id}/reindex` |
| 13 | Cross-tenant negatives | `test_another_tenant_cannot_see_the_acceptance_fixture` | Another `X-Tenant-Id` sees 404 / empty results, never 403 |
| 14 | Railway production smoke | `tests/test_deploy_config.py` (script contract) | `SMOKE_BASE_URL=https://<web>/backend bash scripts/production_smoke.sh` |

Also covered, in smaller tests: `tests/test_api_documents.py`,
`tests/test_processing.py`, `tests/test_relations.py`,
`tests/test_api_search.py`, `tests/test_api_ask.py`,
`tests/test_deletion.py`, `tests/test_reindexing.py`,
`tests/test_api_isolation.py`, `tests/test_evaluation_generation.py`.

Run the slice:

```bash
source .venv/bin/activate
pytest tests/test_mvp_acceptance.py tests/test_deploy_config.py
```

Offline evaluation gates (retrieval + generation, no paid calls):

```bash
python -m app.evaluation
python -m app.evaluation --track generation
```

## Five-minute demo (local)

See the README. Short form:

1. `docker compose up -d && alembic upgrade head`
2. API: `uvicorn app.main:app --reload`
3. Worker: `python -m app.worker`
4. Cockpit: `cd web && npm install && npm run dev`
5. Open http://127.0.0.1:3000, create tenant `demo`, upload the fixture set,
   wait until Ready, search, ask, open a citation, open relations.
6. Re-upload the contract: the UI reports a duplicate.
7. Delete the unrelated file; it disappears from search.
8. Optional: `SMOKE_BASE_URL=http://127.0.0.1:8000 bash scripts/production_smoke.sh`

## Production smoke (item 14)

The script prints `/health` and `/health/ready` status plus check names
(`database`, `vector_store`). It does not print payloads.

```bash
export SMOKE_BASE_URL=https://<web-domain>/backend
bash scripts/production_smoke.sh
```

A green local or mocked run of that script is **not** a live Railway proof.
After the first EU deploy, run it against the public web origin and complete
[SIN-97](https://linear.app/sinan-kahraman/issue/SIN-97/verify-live-railway-production-smoke-and-private-services-after-first).

## Known limitations

* CI never calls OpenAI. Embeddings and answers in the acceptance tests are
  fakes. Live generation eval is opt-in (`python -m app.evaluation --track generation --llm live`) and capped.
* Docling is not exercised by `tests/test_mvp_acceptance.py`. Real PDFs go
  through `tests/test_docling_parser.py` and a local worker with the `parsing`
  extra.
* Production auth is still the tenant header. See SIN-98.
* EU placement of Railway services does not make OpenAI GDPR-compliant by
  itself. See [`PRIVACY.md`](PRIVACY.md).
