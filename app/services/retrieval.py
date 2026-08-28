"""Semantic search over a tenant's chunks.

The vector store answers with identifiers and scores; the text and provenance
come back from PostgreSQL, which is the durable store. That ordering is what
lets the index be rebuilt at any time, and it keeps chunk text out of the
vector database entirely.

The service takes an EmbeddingProvider rather than naming one, so switching
providers is a settings change and not a rewrite.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document
from app.providers.base import EmbeddingProvider
from app.services.vector_store import VectorStoreService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    chunk_id: object
    document_id: object
    document_filename: str
    source_id: str
    text: str
    score: float
    ordinal: int
    page_number: int | None
    section_title: str | None
    source_metadata: dict


async def search(
    session: AsyncSession,
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    *,
    tenant_id,
    query: str,
    limit: int,
    document_ids: list | None = None,
) -> list[SearchHit]:
    """Return the closest chunks to `query` within one tenant.

    `document_ids` narrows the search further; it can never widen it. Ids
    belonging to another tenant simply match nothing, because the tenant
    filter is applied regardless.
    """
    query = query.strip()
    if not query:
        return []

    vectors = await embeddings.embed([query])
    if not vectors:
        return []

    scored = await vector_store.search(
        tenant_id=tenant_id,
        vector=vectors[0],
        limit=limit,
        document_ids=document_ids,
    )
    if not scored:
        return []

    scores = dict(scored)

    # Read the text from PostgreSQL, filtered by tenant again. The vector
    # store having returned an id is not on its own a reason to disclose a row.
    rows = (
        await session.execute(
            select(Chunk, Document.filename)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.tenant_id == tenant_id,
                Chunk.id.in_(list(scores)),
                Document.deleted_at.is_(None),
            )
        )
    ).all()

    hits = [
        SearchHit(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_filename=filename,
            source_id=chunk.source_id,
            text=chunk.text,
            score=scores[chunk.id],
            ordinal=chunk.ordinal,
            page_number=chunk.page_number,
            section_title=chunk.section_title,
            source_metadata=chunk.source_metadata or {},
        )
        for chunk, filename in rows
    ]

    missing = len(scores) - len(hits)
    if missing:
        # The index is ahead of the database: points for chunks that have been
        # deleted. Worth knowing about, never worth failing the search over.
        logger.info(
            "vector store returned points with no durable row",
            extra={"tenant_id": str(tenant_id), "missing": missing},
        )

    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits
