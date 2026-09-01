"""Stop SET NULL from wiping tenant_id on chunk-backed evidence.

A composite ON DELETE SET NULL covering (tenant_id, source_id) tries to null
the non-nullable tenant column when a chunk is removed. Restrict the chunk
FK instead; document deletion drops evidence before chunks.

Revision ID: 0011_chunk_evidence
Revises: 0010_evidence_pkg
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_chunk_evidence"
down_revision: str | None = "0010_evidence_pkg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_evidence_references_tenant_source_id",
        "evidence_references",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_evidence_references_tenant_source_id",
        "evidence_references",
        "chunks",
        ["tenant_id", "source_id"],
        ["tenant_id", "source_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_evidence_references_tenant_source_id",
        "evidence_references",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_evidence_references_tenant_source_id",
        "evidence_references",
        "chunks",
        ["tenant_id", "source_id"],
        ["tenant_id", "source_id"],
        ondelete="SET NULL",
    )
