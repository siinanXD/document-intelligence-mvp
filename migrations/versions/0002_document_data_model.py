"""Create the document data model and the ingestion job queue.

Six tables: tenants, documents, chunks, document_profiles, document_relations
and ingestion_jobs. Every tenant-owned table carries `tenant_id` directly so a
query can filter by tenant without depending on a join, and so uniqueness can
be scoped to one tenant.

Revision ID: 0002_document_data_model
Revises: 0001_initial_baseline
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_document_data_model"
down_revision: str | None = "0001_initial_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Native enum types outlive the tables that use them, so downgrade has to drop
# them explicitly - otherwise a later upgrade fails with "type already exists".
ENUM_TYPES = ("document_status", "job_status", "relation_type")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "ready", "failed", name="document_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("embedding_provider", sa.String(length=64), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_version", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(file_hash) = 64", name="ck_documents_file_hash_sha256"),
        sa.CheckConstraint(
            "content_hash IS NULL OR char_length(content_hash) = 64",
            name="ck_documents_content_hash_sha256",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Exact-duplicate detection is per tenant, and a soft-deleted document must
    # not block re-uploading the same bytes.
    op.create_index(
        "uq_documents_tenant_file_hash_live",
        "documents",
        ["tenant_id", "file_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_documents_tenant_content_hash",
        "documents",
        ["tenant_id", "content_hash"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_documents_tenant_status", "documents", ["tenant_id", "status"], unique=False
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_non_negative"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        sa.UniqueConstraint("tenant_id", "source_id", name="uq_chunks_tenant_source_id"),
    )
    op.create_index(
        "ix_chunks_tenant_document", "chunks", ["tenant_id", "document_id"], unique=False
    )

    op.create_table(
        "document_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "organizations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "persons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "dates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "identifiers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", name="uq_document_profiles_document"),
    )
    op.create_index("ix_document_profiles_tenant", "document_profiles", ["tenant_id"], unique=False)

    op.create_table(
        "document_relations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=False),
        sa.Column("target_document_id", sa.UUID(), nullable=False),
        sa.Column(
            "relation_type",
            sa.Enum(
                "exact_duplicate",
                "content_duplicate",
                "version_of",
                "related",
                name="relation_type",
            ),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_document_relations_score_range",
        ),
        sa.CheckConstraint(
            "source_document_id <> target_document_id",
            name="ck_document_relations_no_self_edge",
        ),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_document_id",
            "target_document_id",
            "relation_type",
            name="uq_document_relations_edge",
        ),
    )
    op.create_index(
        "ix_document_relations_tenant_source",
        "document_relations",
        ["tenant_id", "source_document_id"],
        unique=False,
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "processing", "finished", "failed", name="job_status"),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_ingestion_jobs_attempts_non_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_ingestion_jobs_max_attempts_positive"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # One live job per document: re-queueing a document that is already waiting
    # or running must not put a second worker on it.
    op.create_index(
        "uq_ingestion_jobs_live_document",
        "ingestion_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )
    # The claim query's access path: oldest available queued job first.
    op.create_index(
        "ix_ingestion_jobs_claimable", "ingestion_jobs", ["status", "available_at"], unique=False
    )
    op.create_index("ix_ingestion_jobs_tenant", "ingestion_jobs", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(
        "uq_ingestion_jobs_live_document",
        table_name="ingestion_jobs",
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )
    op.drop_index("ix_ingestion_jobs_tenant", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_claimable", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")

    op.drop_index("ix_document_relations_tenant_source", table_name="document_relations")
    op.drop_table("document_relations")

    op.drop_index("ix_document_profiles_tenant", table_name="document_profiles")
    op.drop_table("document_profiles")

    op.drop_index("ix_chunks_tenant_document", table_name="chunks")
    op.drop_table("chunks")

    op.drop_index(
        "ix_documents_tenant_content_hash",
        table_name="documents",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_documents_tenant_status", table_name="documents")
    op.drop_index(
        "uq_documents_tenant_file_hash_live",
        table_name="documents",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("documents")

    op.drop_table("tenants")

    for enum_name in ENUM_TYPES:
        op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
