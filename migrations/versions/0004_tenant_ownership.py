"""Enforce tenant ownership across document child rows.

Revision ID: 0004_tenant_ownership
Revises: 0003_document_status_queued
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_tenant_ownership"
down_revision: str | None = "0003_document_status_queued"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHILD_FOREIGN_KEYS = (
    ("chunks", "chunks_document_id_fkey", "fk_chunks_tenant_document", "document_id"),
    (
        "document_profiles",
        "document_profiles_document_id_fkey",
        "fk_document_profiles_tenant_document",
        "document_id",
    ),
    (
        "document_relations",
        "document_relations_source_document_id_fkey",
        "fk_document_relations_tenant_source",
        "source_document_id",
    ),
    (
        "document_relations",
        "document_relations_target_document_id_fkey",
        "fk_document_relations_tenant_target",
        "target_document_id",
    ),
    (
        "ingestion_jobs",
        "ingestion_jobs_document_id_fkey",
        "fk_ingestion_jobs_tenant_document",
        "document_id",
    ),
)


def upgrade() -> None:
    op.create_unique_constraint("uq_documents_tenant_id_id", "documents", ["tenant_id", "id"])

    for table, old_name, new_name, document_column in _CHILD_FOREIGN_KEYS:
        op.drop_constraint(old_name, table, type_="foreignkey")
        op.create_foreign_key(
            new_name,
            table,
            "documents",
            ["tenant_id", document_column],
            ["tenant_id", "id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    for table, old_name, new_name, document_column in reversed(_CHILD_FOREIGN_KEYS):
        op.drop_constraint(new_name, table, type_="foreignkey")
        op.create_foreign_key(
            old_name,
            table,
            "documents",
            [document_column],
            ["id"],
            ondelete="CASCADE",
        )

    op.drop_constraint("uq_documents_tenant_id_id", "documents", type_="unique")
