"""Turning a document's stored chunks into searchable vectors.

Indexing is deliberately separate from parsing: it depends on an embedding
provider that costs money and can fail on its own schedule, and it must be
repeatable without duplicating anything.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document
from app.providers.base import EmbeddingProvider
from app.services.vector_store import VectorStoreService

logger = logging.getLogger(__name__)


class IndexingError(RuntimeError):
    """Indexing failed in a way worth retrying."""


@dataclass(frozen=True)
class IndexingOutcome:
    document_id: object
    indexed: int


async def index_document(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    *,
    tenant_id,
    document_id,
) -> IndexingOutcome:
    """Embed and index one document's chunks, replacing any previous vectors.

    Safe to repeat: point ids are the chunk ids, and the document's existing
    points are cleared first, so a reprocessed document cannot leave stale
    vectors answering searches.

    Records which provider, model and version produced the vectors. Without
    that, a later provider change would silently mix two embedding spaces in
    one collection, and nothing would reveal which points needed rebuilding.
    """
    document = (
        (
            await session.execute(
                select(Document).where(
                    Document.id == document_id,
                    Document.tenant_id == tenant_id,
                    Document.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    if document is None:
        raise IndexingError("the document to index no longer exists")

    chunks = list(
        (
            await session.execute(
                select(Chunk)
                .where(Chunk.tenant_id == tenant_id, Chunk.document_id == document_id)
                .order_by(Chunk.ordinal)
            )
        ).scalars()
    )

    await vector_store.ensure_collection(dimensions=embeddings.dimensions)

    if not chunks:
        # Still clear whatever was indexed before: a document that now parses
        # to nothing must stop answering searches.
        await vector_store.delete_document(tenant_id=tenant_id, document_id=document_id)
        _record_provider(document, embeddings)
        await session.flush()
        return IndexingOutcome(document_id=document_id, indexed=0)

    try:
        vectors = await embeddings.embed([chunk.text for chunk in chunks])
    except Exception as exc:
        # The provider's message can quote the text it was given.
        raise IndexingError(f"embedding failed ({type(exc).__name__})") from None

    if len(vectors) != len(chunks):
        raise IndexingError(f"embedding returned {len(vectors)} vectors for {len(chunks)} chunks")

    indexed = await vector_store.upsert_chunks(
        tenant_id=tenant_id,
        document_id=document_id,
        points=[
            (chunk.id, vector, chunk.source_id)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
    )

    _record_provider(document, embeddings)
    await session.flush()

    logger.info(
        "document indexed",
        extra={
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "chunk_count": indexed,
        },
    )
    return IndexingOutcome(document_id=document_id, indexed=indexed)


def _record_provider(document: Document, embeddings: EmbeddingProvider) -> None:
    document.embedding_provider = embeddings.provider
    document.embedding_model = embeddings.model
    document.embedding_version = embeddings.version
