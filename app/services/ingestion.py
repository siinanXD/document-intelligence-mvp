"""Turning an upload into a document, a stored object and a queued job."""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus
from app.providers.storage import StorageBackend
from app.services import documents as documents_service
from app.services import jobs as jobs_service
from app.services.uploads import ValidatedUpload, storage_key_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    document: Document
    is_duplicate: bool


async def ingest_upload(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    tenant_id,
    upload: ValidatedUpload,
    content: bytes,
) -> IngestionResult:
    """Persist an upload, or return the document that already holds these bytes.

    Duplicate detection is scoped to the tenant and happens before anything
    expensive: identical bytes are never stored twice and never re-queued.

    Ordering matters on the unhappy path. The object is written before the
    transaction commits, so a failure at commit leaves an unreferenced object
    in storage rather than a document row pointing at nothing. Garbage in a
    bucket is recoverable; a dangling reference is not.
    """
    # Serialize identical uploads for one tenant inside the database transaction.
    # A check followed by an insert is otherwise racy: two requests can both
    # observe "missing", and the loser only discovers the unique constraint at
    # flush time. The transaction lock lets the loser observe the winner's
    # committed row and return the normal duplicate response.
    duplicate_lock_key = f"upload:{tenant_id}:{upload.file_hash}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": duplicate_lock_key},
    )

    existing = await documents_service.find_by_file_hash(
        session, tenant_id=tenant_id, file_hash=upload.file_hash
    )
    if existing is not None:
        logger.info(
            "upload matched an existing document",
            extra={"tenant_id": str(tenant_id), "document_id": str(existing.id)},
        )
        return IngestionResult(document=existing, is_duplicate=True)

    # The id is generated here rather than by the database so the storage key
    # is known before the insert. Writing the row once keeps `updated_at` from
    # being invalidated by a second flush, which would then need a round trip
    # to read back.
    document_id = uuid.uuid4()
    storage_key = storage_key_for(
        tenant_id=tenant_id, document_id=document_id, filename=upload.filename
    )
    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        filename=upload.filename,
        mime_type=upload.mime_type,
        file_hash=upload.file_hash,
        storage_key=storage_key,
        status=DocumentStatus.queued,
    )
    session.add(document)
    await session.flush()

    await storage.put(storage_key, content, content_type=upload.mime_type)

    await jobs_service.enqueue(session, tenant_id=tenant_id, document_id=document.id)

    logger.info(
        "document queued for ingestion",
        extra={"tenant_id": str(tenant_id), "document_id": str(document.id)},
    )
    return IngestionResult(document=document, is_duplicate=False)
