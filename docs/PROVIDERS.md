# AI providers: embeddings and optional reranking

The application never names a vendor outside the provider layer. Routes and
services ask the registry (`app/providers/registry.py`) for a capability, and
settings decide which implementation answers. This document describes the two
embedding providers, the reindex a provider change requires, and the optional
reranker.

## Embedding providers

### OpenAI (default)

```bash
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small     # or text-embedding-3-large
OPENAI_API_KEY=...                         # from the environment, never committed
```

Model dimensionality is known to the provider (`1536` / `3072`); an unknown
model name fails at construction rather than guessing.

### Hugging Face / Text Embeddings Inference-compatible

Any server that speaks the [Text Embeddings Inference](https://github.com/huggingface/text-embeddings-inference)
HTTP protocol (`POST /embed`) works: a local TEI container, a GPU host, or a
hosted inference endpoint. The application only ever sees a base URL.

```bash
EMBEDDING_PROVIDER=huggingface
EMBEDDING_VERSION=v1
HUGGINGFACE_EMBEDDINGS_BASE_URL=http://embeddings.internal:8080
HUGGINGFACE_EMBEDDING_MODEL=BAAI/bge-m3     # required identity marker, persisted with vectors
HUGGINGFACE_EMBEDDING_DIMENSIONS=1024      # required; verified on every response
HUGGINGFACE_API_KEY=                       # optional bearer token
HUGGINGFACE_TIMEOUT_SECONDS=30
HUGGINGFACE_EMBEDDING_BATCH_SIZE=32
```

A TEI endpoint serves one fixed model, so the model name here is an identity
marker rather than a request parameter. It is persisted with every indexed
document (provider, model, version, dimensions), which is what makes a
configuration change detectable instead of silently mixing embedding spaces.

`HUGGINGFACE_EMBEDDING_MODEL` is deliberately separate from the OpenAI
`EMBEDDING_MODEL` setting, so selecting Hugging Face cannot inherit an OpenAI
default as its stored identity.

`HUGGINGFACE_EMBEDDING_DIMENSIONS` must be declared, never guessed: every
response is checked against it, so a wrong value (or an endpoint swapped to a
different model) fails before any vector write. The vector collection itself
is also checked - an existing collection of a different width is an error, not
a recreation.

### Candidate multilingual models

These are documented as candidates, not hard-coded anywhere in domain logic.
Any TEI-compatible model works; pick per deployment and set the three
Hugging Face variables above accordingly.

| Model | Dimensions | Notes |
| --- | --- | --- |
| `BAAI/bge-m3` | 1024 | Strong multilingual retrieval, long inputs (8k) |
| `Qwen/Qwen3-Embedding-0.6B` | 1024 | Multilingual, light enough for CPU/small GPU |
| `Qwen/Qwen3-Embedding-4B` / `-8B` | 2560 / 4096 | Higher quality, needs a GPU host |
| `intfloat/multilingual-e5-large` | 1024 | Well-understood multilingual baseline |

Verify the dimension against the model card of the exact revision you deploy;
the table is a starting point, not a source of truth.

## Switching providers: configuration plus a reindex

Switching embeddings is a configuration change **plus a reindex**. No code
changes, no re-upload, no re-parse: chunks live in PostgreSQL and vectors are
rebuilt from them.

1. Deploy the new configuration (see variables above).
2. Rebuild the vectors for each tenant from stored chunks:

   ```bash
   # per document
   curl -X POST -H "X-Tenant-Id: <tenant>" /documents/<id>/reindex
   # or the whole tenant
   curl -X POST -H "X-Tenant-Id: <tenant>" /documents/reindex
   ```

3. If the new provider has a **different dimensionality**, the existing Qdrant
   collections are the wrong width. Reindexing fails closed with a dimension
   error rather than writing into them. Drop (or rename via
   `QDRANT_COLLECTION` / `QDRANT_DOCUMENTS_COLLECTION`) the old collections,
   then reindex; the collection is recreated at the new width on first write.

Until a document is reindexed, search does not pretend it is current: stored
vectors carry the identity of the provider that produced them, and hits whose
identity does not match the active provider are dropped rather than ranked
against a query embedded in a different space. The behavior is covered by
`tests/test_reindexing.py`.

## Optional reranker

Reranking is a second retrieval stage: the first stage fetches a wider
candidate set from the vector store, a cross-encoder scores each passage
against the query, and the same `limit` is applied to the reranked order. It
is **disabled by default**, changes no API contract when enabled, and a
reranker failure degrades to the vector ranking instead of failing the search.

```bash
RERANKER_PROVIDER=huggingface              # default: none
RERANKER_MODEL=BAAI/bge-reranker-v2-m3     # identity marker
HUGGINGFACE_RERANK_BASE_URL=http://rerank.internal:8080
RERANKER_CANDIDATE_MULTIPLIER=4            # first stage fetches limit * this
RERANKER_MAX_CANDIDATES=50                 # cap on the candidate set
```

The implementation speaks the TEI `POST /rerank` protocol. Candidate reranker
models include `BAAI/bge-reranker-v2-m3` (multilingual) and the Qwen3-Reranker
family. Passages are sent to the configured endpoint at query time; when that
endpoint is not inside your own trust boundary, treat it with the same care as
the embedding endpoint.

## Hosting: where the models run is not the application's concern

Railway hosts the API, worker, PostgreSQL and Qdrant. Larger models - GPU
embeddings, rerankers, or a future local LLM - typically do not fit there, and
they do not have to: the application consumes provider endpoints through base
URLs, so serving infrastructure can live anywhere reachable from the API.

Typical setups:

- **Local development**: TEI containers next to `docker compose` services,
  e.g. `ghcr.io/huggingface/text-embeddings-inference` with `--model-id
  BAAI/bge-m3`, and the base URLs pointing at `http://localhost:<port>`.
- **Self-hosted GPU outside Railway**: a GPU box or rented instance runs
  TEI/TGI; the Railway API reaches it over a private network or an
  authenticated HTTPS endpoint (`HUGGINGFACE_API_KEY` becomes the bearer
  token). Keep the endpoint in the EU if the deployment's privacy posture
  requires it (see `docs/PRIVACY.md`).
- **Hosted inference**: a managed TEI-compatible endpoint; same configuration,
  different URL.

None of this appears in domain code. Ingestion, retrieval and the API behave
identically under either provider - the test suite runs the same flows against
mocked OpenAI and mocked Hugging Face providers, and CI never makes a paid
call.
