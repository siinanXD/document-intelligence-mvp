"""Idempotent deletion of a tenant's document and everything derived from it.

The document row is soft-deleted so the same bytes can be uploaded again
(the live unique index ignores `deleted_at`). Everything that could answer a
search, citation or relation is actually removed: object storage, chunks,
profile, relations, jobs, and both Qdrant collections.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engineering_models import PackageAssignment, PackageDocument
from app.models import Chunk, Document, DocumentProfile, DocumentRelation, IngestionJob
from app.providers.storage import StorageBackend
from app.services.engineering import drop_document_evidence
from app.services.package_intake import delete_adapter_artifacts
from app.services.vector_store import DocumentVectorStore, VectorStoreService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeletionOutcome:
    document_id: object
    already_deleted: bool


async def delete_document(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    tenant_id,
    document_id,
    vector_store: VectorStoreService | None = None,
    document_vectors: DocumentVectorStore | None = None,
) -> DeletionOutcome | None:
    """Remove one document for this tenant, or report that it was already gone.

    Returns None when no such document exists for this tenant - including when
    the id belongs to someone else. A second call for a document this tenant
    already deleted still runs leftover cleanup and returns success.

    The row is locked before any storage or index work. An in-flight worker
    that already holds the document either finishes first - this then removes
    whatever it wrote - or waits and then sees `deleted_at` and skips.
    """
    document = (
        (
            await session.execute(
                select(Document)
                .where(Document.id == document_id, Document.tenant_id == tenant_id)
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if document is None:
        return None

    already_deleted = document.deleted_at is not None

    if vector_store is not None:
        await vector_store.delete_document(tenant_id=tenant_id, document_id=document_id)
    if document_vectors is not None:
        await document_vectors.delete(tenant_id=tenant_id, document_id=document_id)

    await storage.delete(document.storage_key)
    if document.normalized_key:
        await storage.delete(document.normalized_key)
    await delete_adapter_artifacts(storage, document=document)

    await drop_document_evidence(session, tenant_id=tenant_id, document_id=document_id)

    await session.execute(
        delete(Chunk).where(Chunk.tenant_id == tenant_id, Chunk.document_id == document_id)
    )
    await session.execute(
        delete(DocumentProfile).where(
            DocumentProfile.tenant_id == tenant_id, DocumentProfile.document_id == document_id
        )
    )
    await session.execute(
        delete(DocumentRelation).where(
            DocumentRelation.tenant_id == tenant_id,
            or_(
                DocumentRelation.source_document_id == document_id,
                DocumentRelation.target_document_id == document_id,
            ),
        )
    )
    await session.execute(
        delete(IngestionJob).where(
            IngestionJob.tenant_id == tenant_id, IngestionJob.document_id == document_id
        )
    )
    await session.execute(
        delete(PackageAssignment).where(
            PackageAssignment.tenant_id == tenant_id,
            PackageAssignment.document_id == document_id,
        )
    )
    await session.execute(
        delete(PackageDocument).where(
            PackageDocument.tenant_id == tenant_id, PackageDocument.document_id == document_id
        )
    )

    if not already_deleted:
        document.deleted_at = datetime.now(UTC)
        document.updated_at = datetime.now(UTC)

    await session.flush()

    logger.info(
        "document deleted",
        extra={
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "already_deleted": already_deleted,
        },
    )
    return DeletionOutcome(document_id=document_id, already_deleted=already_deleted)
