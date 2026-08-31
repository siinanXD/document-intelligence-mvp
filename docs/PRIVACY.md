# Privacy notes for the document intelligence MVP.

This document describes what the service stores, what it derives, which
external systems it calls, how deletion and reindexing work, and what is
allowed into logs. It is the retention/deletion note required by the security
milestone; it is not a legal policy.

## Stored data

Every tenant-owned row carries `tenant_id`. Queries take that identifier as an
explicit argument; another tenant's row is absent, not forbidden.

**PostgreSQL** is the source of truth:

* Original upload metadata on `documents`: filename, MIME type, hashes, storage
  keys, status, embedding identity (provider, model, version, dimensions).
* Chunk text and provenance on `chunks` (page, section, `source_id`).
* Document profiles (language, summary, entities, identifiers, topics).
* Directed document relations and the signals that produced them.
* Ingestion jobs and their diagnostic `last_error` (type and short reason;
  never document text).

A live unique index on `(tenant_id, file_hash)` ignores soft-deleted rows, so
deleting a document frees that hash for a later upload of the same bytes.
Uniqueness is always per tenant: two tenants uploading identical bytes are not
duplicates of each other.

**Object storage** holds:

* The original uploaded bytes, addressed by `storage_key`.
* The parser's serialized representation (Docling JSON), addressed by
  `normalized_key`, so a later reindex can start from parsed output rather than
  converting the original again.

**Qdrant** is an index, not a store of documents. Chunk and document points
carry identifiers only (`tenant_id`, `document_id`, `chunk_id`, `source_id`).
They do not carry chunk text or filenames. Losing the collection costs a
reindex, not data; a breach of it yields ids rather than customer documents.

## Derived artifacts

Ingestion parses the original object, writes the normalized artifact, replaces
PostgreSQL chunks, embeds them, upserts Qdrant points, extracts a profile, and
detects relations. Reprocessing replaces derived data rather than appending to
it. A document is `ready` only once its chunks are searchable.

## External providers

Call sites ask the registry for an embedding or LLM capability; they do not
name a vendor. The configured provider receives chunk text (to embed) or a
prompt assembled from retrieved passages (to answer). Provider, model, version
and dimensions are stored on the document so a later change is detectable.

CI and the default test suite mock every paid provider. Ordinary CI never
makes a billed API call.

## Deletion

`DELETE /documents/{id}` is tenant-scoped and idempotent. Another tenant's
document is a 404. Repeating the call after a successful delete is still 204.

A delete removes or makes inaccessible:

* the original stored object
* the stored normalized/Docling artifact
* PostgreSQL chunks, profile, relations and ingestion jobs
* Qdrant chunk points
* the Qdrant document vector

The document row is soft-deleted (`deleted_at`). Live list/get/search/ask
paths ignore it. The original hash may be uploaded again.

The document row is locked (`SELECT ... FOR UPDATE`) before any storage or
index work, and the ingestion worker takes the same lock before it writes
derived data. Whichever transaction commits first, the other then sees the
truth: the worker skips a deleted document, or the deleter waits and then
removes the worker's freshly committed chunks, profile, relations, artifact
and vectors. A second `DELETE` is still idempotent leftover cleanup.

Object storage and Qdrant are cleaned before the request session commits. If
that commit then fails, the client gets an error (not a false 204) and the
row stays live while its artifacts are already gone. A retried delete
converges: both storage backends treat a missing object as success.

Deletion does not by itself purge database backups, object-store versioning, or
Qdrant snapshots. Those follow the backup retention of the environment they
run in.

## Reindexing

`POST /documents/{id}/reindex` and `POST /reindex` rebuild vectors from stored
PostgreSQL chunks. They do not require a new upload and they do not re-parse
the original bytes. Point ids are chunk ids; existing points for the document
are cleared first, so a reindex cannot leave duplicate results.

A document with no stored chunks cannot be reindexed that way: there is
nothing to embed. That case needs the ingestion worker, which still has the
original object and the normalized artifact.

The tenant-level path walks that tenant's live ready documents and skips ones
without chunks (`skipped`). A provider or indexing failure is counted as
`failed`, not skipped, and a single-document reindex of that case is 503
rather than 409. It does not touch another tenant's vectors.

## Embedding identity and vector spaces

The Qdrant collection has a fixed vector size. `ensure_collection` refuses to
use an existing collection whose size does not match the current embedding
provider. Changing dimensions therefore cannot silently mix incompatible
spaces; the collection has to be recreated and documents reindexed.

Provider, model, version and dimensions are persisted on each document.
Semantic search hydrates hits from PostgreSQL and drops any whose stored
identity does not match the current provider, so a model change cannot return
stale points as if they were comparable. Leftover stale Qdrant points still
occupy the search top-k until reindex finishes, so a partial (or aborted)
same-width model change can yield empty search results even though current
chunks exist. After changing the embedding model, run the tenant reindex (or
recreate the collection if the size also changed) before treating recall as
representative.

## Logging and privacy

Request logs may contain:

* `request_id`
* `tenant_id`
* `document_id`
* `job_id`
* `duration_ms`
* `status`
* `error_type`
* method and path (without the query string)

They must not contain, by default:

* document or chunk bodies
* full prompts
* complete model responses
* credentials, API keys, or `Authorization` headers
* uploaded filenames of customer data
* request bodies

Health probes are not audit-logged. Application log lines that record
ingestion, indexing, search or answering carry identifiers and counts, not
the text that caused them. A parser exception is converted to its type before
it is stored on a job or written to a log.

This is identification, not authentication: `X-Tenant-Id` names the tenant.
Real credentials are a later hardening step; until then the header is trusted
and must not be treated as a secret in logs either — only as an identifier.
