"""ORM models: the durable source of truth for the MVP.

Every tenant-owned row carries `tenant_id` directly, including rows that could
reach it through a join. The duplication is deliberate: it lets every query
filter by tenant without relying on a join being written correctly, and it
keeps a uniqueness constraint scopeable to one tenant.

PostgreSQL stays authoritative even for data that is also indexed in Qdrant.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text  # aliased: Chunk.text shadows it
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class DocumentStatus(enum.StrEnum):
    """Lifecycle of a document from upload to searchable."""

    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class JobStatus(enum.StrEnum):
    """Lifecycle of an ingestion job."""

    queued = "queued"
    processing = "processing"
    finished = "finished"
    failed = "failed"


class RelationType(enum.StrEnum):
    """How one document relates to another."""

    exact_duplicate = "exact_duplicate"
    content_duplicate = "content_duplicate"
    version_of = "version_of"
    related = "related"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)

    # SHA-256 of the uploaded bytes, known before any expensive processing.
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 of the deterministically normalized text, known after parsing.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", native_enum=True),
        nullable=False,
        default=DocumentStatus.pending,
        server_default=DocumentStatus.pending.value,
    )

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Persisted with the document so a provider change is detectable rather
    # than silently mixing two embedding spaces in one collection.
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # Exact-duplicate detection is per tenant, and a soft-deleted document
        # must not block re-uploading the same bytes.
        Index(
            "uq_documents_tenant_file_hash_live",
            "tenant_id",
            "file_hash",
            unique=True,
            postgresql_where=sql_text("deleted_at IS NULL"),
        ),
        Index(
            "ix_documents_tenant_content_hash",
            "tenant_id",
            "content_hash",
            postgresql_where=sql_text("deleted_at IS NULL"),
        ),
        Index("ix_documents_tenant_status", "tenant_id", "status"),
        CheckConstraint("char_length(file_hash) = 64", name="ck_documents_file_hash_sha256"),
        CheckConstraint(
            "content_hash IS NULL OR char_length(content_hash) = 64",
            name="ck_documents_content_hash_sha256",
        ),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Provenance: what a citation points back to.
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stable across reindexing, so a citation survives a provider change.
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _created_at()

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        UniqueConstraint("tenant_id", "source_id", name="uq_chunks_tenant_source_id"),
        Index("ix_chunks_tenant_document", "tenant_id", "document_id"),
        CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_non_negative"),
    )


class DocumentProfile(Base):
    """What a document is, extracted once and grounded in its content."""

    __tablename__ = "document_profiles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Absent information stays an empty list; it is never invented.
    organizations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    persons: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    dates: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    identifiers: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )
    topics: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("document_id", name="uq_document_profiles_document"),
        Index("ix_document_profiles_tenant", "tenant_id"),
    )


class DocumentRelation(Base):
    """A directed relationship between two documents of the same tenant."""

    __tablename__ = "document_relations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    target_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    relation_type: Mapped[RelationType] = mapped_column(
        Enum(RelationType, name="relation_type", native_enum=True), nullable=False
    )
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_document_id",
            "target_document_id",
            "relation_type",
            name="uq_document_relations_edge",
        ),
        Index("ix_document_relations_tenant_source", "tenant_id", "source_document_id"),
        CheckConstraint(
            "source_document_id <> target_document_id",
            name="ck_document_relations_no_self_edge",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_document_relations_score_range",
        ),
    )


class IngestionJob(Base):
    """A unit of ingestion work, claimed from PostgreSQL rather than Redis."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=True),
        nullable=False,
        default=JobStatus.queued,
        server_default=JobStatus.queued.value,
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=sql_text("3")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # A job is invisible to claimers until this moment, which is how retry
    # backoff is expressed without a scheduler.
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    __table_args__ = (
        # One live job per document: re-queueing a document that is already
        # waiting or running must not create a second worker for it.
        Index(
            "uq_ingestion_jobs_live_document",
            "document_id",
            unique=True,
            postgresql_where=sql_text("status IN ('queued', 'processing')"),
        ),
        # The claim query's access path: oldest available queued job first.
        Index("ix_ingestion_jobs_claimable", "status", "available_at"),
        Index("ix_ingestion_jobs_tenant", "tenant_id"),
        CheckConstraint("attempts >= 0", name="ck_ingestion_jobs_attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="ck_ingestion_jobs_max_attempts_positive"),
    )
