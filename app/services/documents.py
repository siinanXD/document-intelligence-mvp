"""Tenant-scoped access to documents and chunks.

Every function here takes `tenant_id` as an explicit argument and filters on
it. None of them read it from ambient state, and none offer an unscoped
variant: a caller that wants another tenant's row has to write a new query and
justify it in review, rather than forgetting a filter.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document


async def get_document(session: AsyncSession, *, tenant_id, document_id) -> Document | None:
    """Return one live document. Another tenant's document reads as absent."""
    result = await session.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == tenant_id,
            Document.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def list_documents(
    session: AsyncSession, *, tenant_id, limit: int = 50, offset: int = 0
) -> list[Document]:
    """List a tenant's live documents, newest first."""
    result = await session.execute(
        select(Document)
        .where(Document.tenant_id == tenant_id, Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def find_by_file_hash(session: AsyncSession, *, tenant_id, file_hash: str) -> Document | None:
    """Find an exact-duplicate upload within one tenant.

    Two tenants uploading identical bytes are not duplicates of each other, so
    this can only ever match inside the caller's own tenant.
    """
    result = await session.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.file_hash == file_hash,
            Document.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def find_by_content_hash(
    session: AsyncSession, *, tenant_id, content_hash: str
) -> list[Document]:
    """Find documents whose normalized content matches, within one tenant."""
    result = await session.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.content_hash == content_hash,
            Document.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def list_chunks(session: AsyncSession, *, tenant_id, document_id) -> list[Chunk]:
    """Return a document's chunks in order, scoped to one tenant."""
    result = await session.execute(
        select(Chunk)
        .where(Chunk.tenant_id == tenant_id, Chunk.document_id == document_id)
        .order_by(Chunk.ordinal)
    )
    return list(result.scalars().all())


async def count_chunks(session: AsyncSession, *, tenant_id, document_id) -> int:
    """Count a document's chunks, scoped to one tenant."""
    result = await session.execute(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.tenant_id == tenant_id, Chunk.document_id == document_id)
    )
    return int(result.scalar_one())
