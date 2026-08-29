"""Processing one ingestion job: parse, normalize, chunk, record.

The whole of a job's work happens in one database transaction, so a document
never ends up `ready` with someone else's chunks, or half-replaced ones.
Reprocessing is therefore safe to repeat: existing derived data is deleted and
rewritten rather than appended to.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document, DocumentStatus, IngestionJob
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.parsing import DocumentParser, ParsingError
from app.providers.storage import ObjectNotFoundError, StorageBackend
from app.services import jobs as jobs_service
from app.services.document_vectors import get_document_vector_strategy
from app.services.indexing import IndexingError, index_document
from app.services.normalization import content_hash, normalize_text, source_id_for
from app.services.profiling import ProfilingError, profile_document
from app.services.relations import detect_relations
from app.services.vector_store import (
    DocumentVectorStore,
    VectorStoreError,
    VectorStoreService,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingOutcome:
    document_id: object
    chunk_count: int
    content_duplicate_of: object | None
    indexed: int = 0
    profiled: bool = False
    relations: int = 0


def normalized_key_for(document: Document) -> str:
    """Where the parsed representation lives, beside the original object."""
    return f"{document.storage_key}.docling.json"


async def process_job(
    session: AsyncSession,
    storage: StorageBackend,
    parser: DocumentParser,
    *,
    job: IngestionJob,
    embeddings: EmbeddingProvider | None = None,
    vector_store: VectorStoreService | None = None,
    llm: LLMProvider | None = None,
    document_vectors: DocumentVectorStore | None = None,
) -> ProcessingOutcome:
    """Take one claimed job from `processing` to a finished document.

    Raises ParsingError or ObjectNotFoundError for the caller to record as a
    retryable failure; anything else propagates unchanged.
    """
    document = (
        (
            await session.execute(
                select(Document).where(
                    Document.id == job.document_id, Document.tenant_id == job.tenant_id
                )
            )
        )
        .scalars()
        .first()
    )
    if document is None:
        raise ParsingError("the job's document no longer exists")

    document.status = DocumentStatus.processing
    await session.flush()

    content = await storage.get(document.storage_key)
    parsed = await parser.parse(
        filename=document.filename, mime_type=document.mime_type, content=content
    )

    normalized = normalize_text(parsed.text)
    digest = content_hash(parsed.text)

    # A content duplicate is recorded, not suppressed: the two documents are
    # different uploads that happen to say the same thing, and which of them a
    # user cares about is not ours to decide. Scoped to the tenant, always.
    duplicate_of = None
    if normalized:
        duplicate_of = (
            (
                await session.execute(
                    select(Document.id).where(
                        Document.tenant_id == document.tenant_id,
                        Document.content_hash == digest,
                        Document.id != document.id,
                        Document.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )

    normalized_key = normalized_key_for(document)
    await storage.put(normalized_key, parsed.serialized, content_type="application/json")

    # Replace, never append: reprocessing must not leave stale chunks behind.
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))

    for chunk in parsed.chunks:
        session.add(
            Chunk(
                tenant_id=document.tenant_id,
                document_id=document.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                source_id=source_id_for(document_id=document.id, ordinal=chunk.ordinal),
                source_metadata=chunk.provenance,
            )
        )

    document.content_hash = digest
    document.normalized_key = normalized_key
    document.parser_name = parser.name
    await session.flush()

    # Indexing shares the job's transaction: a document reads `ready` only once
    # its chunks are searchable, so nothing can be listed as ready and then
    # return nothing when searched.
    indexed = 0
    chunk_vectors: list[list[float]] = []
    if embeddings is not None and vector_store is not None:
        outcome = await index_document(
            session,
            embeddings,
            vector_store,
            tenant_id=document.tenant_id,
            document_id=document.id,
        )
        indexed = outcome.indexed
        chunk_vectors = outcome.chunk_vectors

    # Profiling is what makes relation detection more than a similarity score:
    # shared parties and identifiers come from here.
    profiled = False
    if llm is not None:
        await profile_document(session, llm, tenant_id=document.tenant_id, document_id=document.id)
        profiled = True

    relations = 0
    if document_vectors is not None and embeddings is not None:
        strategy = get_document_vector_strategy()
        document_vector = strategy.combine(chunk_vectors)
        await document_vectors.ensure_collection(dimensions=embeddings.dimensions)
        if document_vector is not None:
            await document_vectors.upsert(
                tenant_id=document.tenant_id,
                document_id=document.id,
                vector=document_vector,
            )
        else:
            # Nothing to compare with: clear any vector from a previous run
            # rather than leaving a stale one answering for this document.
            await document_vectors.delete(tenant_id=document.tenant_id, document_id=document.id)
        relations = len(
            await detect_relations(
                session,
                document_vectors,
                tenant_id=document.tenant_id,
                document_id=document.id,
                vector=document_vector,
            )
        )

    document.status = DocumentStatus.ready
    await session.flush()

    logger.info(
        "document processed",
        extra={
            "tenant_id": str(document.tenant_id),
            "document_id": str(document.id),
            "chunk_count": len(parsed.chunks),
            "indexed": indexed,
            "relations": relations,
        },
    )
    return ProcessingOutcome(
        document_id=document.id,
        chunk_count=len(parsed.chunks),
        content_duplicate_of=duplicate_of,
        indexed=indexed,
        profiled=profiled,
        relations=relations,
    )


async def mark_failed(
    session: AsyncSession, *, job: IngestionJob, worker_id: str, reason: str, retry_delay
) -> None:
    """Record a failed attempt on both the job and its document.

    `worker_id` must be the worker holding the claim: the queue only transitions
    a job for the worker that took it, so a stale or foreign worker changes
    nothing here either - `updated` comes back None and the document is left
    alone.

    `reason` is a short diagnostic. It must never carry document text: the
    parser is responsible for not putting any into its exception messages, and
    this is the second place that would leak it.
    """
    document = (
        (
            await session.execute(
                select(Document).where(
                    Document.id == job.document_id, Document.tenant_id == job.tenant_id
                )
            )
        )
        .scalars()
        .first()
    )

    updated = await jobs_service.fail(
        session,
        tenant_id=job.tenant_id,
        job_id=job.id,
        worker_id=worker_id,
        error=reason,
        retry_delay=retry_delay,
    )

    # The document only reads `failed` once no attempt remains; while retries
    # are pending it stays queued, which is what it actually is.
    if document is not None and updated is not None:
        document.status = (
            DocumentStatus.failed if updated.status.value == "failed" else DocumentStatus.queued
        )
    await session.flush()

    logger.warning(
        "ingestion attempt failed",
        extra={"tenant_id": str(job.tenant_id), "job_id": str(job.id), "reason": reason},
    )


RETRYABLE_ERRORS = (
    ParsingError,
    ObjectNotFoundError,
    IndexingError,
    ProfilingError,
    VectorStoreError,
)
