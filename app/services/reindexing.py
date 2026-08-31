"""Rebuild vectors from stored derived data, without a new upload.

Indexing already knows how to embed a document's PostgreSQL chunks and replace
Qdrant points. Reindexing is that path, plus the document-level vector, run
for one document or for every live document of a tenant. Parsing the original
bytes again is a different job - the worker - and is not invoked here.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus
from app.providers.base import EmbeddingProvider
from app.services.document_vectors import get_document_vector_strategy
from app.services.indexing import IndexingError, index_document, is_current_embedding
from app.services.vector_store import DocumentVectorStore, VectorStoreError, VectorStoreService

logger = logging.getLogger(__name__)


class ReindexError(RuntimeError):
    """The document cannot be reindexed from stored derived data."""


class ReindexNotFound(ReindexError):
    """No live document for this tenant."""


class NoStoredChunks(ReindexError):
    """The document has no derived chunks to embed."""


@dataclass(frozen=True)
class ReindexOutcome:
    document_id: object
    indexed: int
    stale_before: bool


@dataclass(frozen=True)
class TenantReindexOutcome:
    tenant_id: object
    reindexed: int
    skipped: int
    failed: int
    documents: list[ReindexOutcome] = field(default_factory=list)


async def reindex_document(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    *,
    tenant_id,
    document_id,
    document_vectors: DocumentVectorStore | None = None,
) -> ReindexOutcome:
    """Replace one document's vectors from its stored chunks.

    The original upload is not read. A document with no chunks has nothing to
    rebuild from and is an error rather than a silent empty index - that case
    needs the worker, not reindexing.
    """
    document = (
        (
            await session.execute(
                select(Document)
                .where(
                    Document.id == document_id,
                    Document.tenant_id == tenant_id,
                    Document.deleted_at.is_(None),
                )
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if document is None:
        raise ReindexNotFound("the document to reindex no longer exists")

    stale_before = not is_current_embedding(document, embeddings)

    if document_vectors is not None:
        # Fail closed on an incompatible document-vector collection before
        # rewriting chunk points, so a 503 cannot leave Qdrant ahead of Postgres.
        await document_vectors.ensure_collection(dimensions=embeddings.dimensions)

    try:
        outcome = await index_document(
            session,
            embeddings,
            vector_store,
            tenant_id=tenant_id,
            document_id=document_id,
        )
    except IndexingError as exc:
        raise ReindexError(str(exc)) from None

    if outcome.indexed == 0:
        raise NoStoredChunks("the document has no stored chunks to reindex")

    if document_vectors is not None:
        strategy = get_document_vector_strategy()
        await document_vectors.ensure_collection(dimensions=embeddings.dimensions)
        document_vector = strategy.combine(outcome.chunk_vectors)
        if document_vector is not None:
            await document_vectors.upsert(
                tenant_id=tenant_id, document_id=document_id, vector=document_vector
            )
        else:
            await document_vectors.delete(tenant_id=tenant_id, document_id=document_id)

    logger.info(
        "document reindexed",
        extra={
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "indexed": outcome.indexed,
            "stale_before": stale_before,
        },
    )
    return ReindexOutcome(
        document_id=document_id, indexed=outcome.indexed, stale_before=stale_before
    )


async def reindex_tenant(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    *,
    tenant_id,
    document_vectors: DocumentVectorStore | None = None,
) -> TenantReindexOutcome:
    """Rebuild vectors for every live, ready document of one tenant.

    Documents without stored chunks are skipped, not failed: they were never
    indexed and reindexing cannot invent chunks. A provider or indexing
    failure is counted as failed so an operator is not told the corpus is
    current. A dimension mismatch fails closed on the first document rather
    than mixing spaces.
    """
    documents = list(
        (
            await session.execute(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.deleted_at.is_(None),
                    Document.status == DocumentStatus.ready,
                )
                .order_by(Document.created_at)
            )
        ).scalars()
    )

    results: list[ReindexOutcome] = []
    skipped = 0
    failed = 0
    for document in documents:
        try:
            results.append(
                await reindex_document(
                    session,
                    embeddings,
                    vector_store,
                    tenant_id=tenant_id,
                    document_id=document.id,
                    document_vectors=document_vectors,
                )
            )
        except NoStoredChunks:
            skipped += 1
            logger.info(
                "document skipped during tenant reindex",
                extra={
                    "tenant_id": str(tenant_id),
                    "document_id": str(document.id),
                },
            )
        except VectorStoreError:
            # An incompatible collection cannot be repaired per-document; stop
            # rather than leaving a mix of old and new spaces.
            raise
        except ReindexError as exc:
            failed += 1
            logger.warning(
                "document failed during tenant reindex",
                extra={
                    "tenant_id": str(tenant_id),
                    "document_id": str(document.id),
                    "error_type": type(exc).__name__,
                },
            )
        except Exception as exc:
            failed += 1
            logger.warning(
                "document failed during tenant reindex",
                extra={
                    "tenant_id": str(tenant_id),
                    "document_id": str(document.id),
                    "error_type": type(exc).__name__,
                },
            )

    logger.info(
        "tenant reindexed",
        extra={
            "tenant_id": str(tenant_id),
            "reindexed": len(results),
            "skipped": skipped,
            "failed": failed,
        },
    )
    return TenantReindexOutcome(
        tenant_id=tenant_id,
        reindexed=len(results),
        skipped=skipped,
        failed=failed,
        documents=results,
    )
