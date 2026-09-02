"""Add explicit engineering document class and member path on assignments.

Revision ID: 0012_document_class
Revises: 0011_chunk_evidence
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_document_class"
down_revision: str | None = "0011_chunk_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENT_CLASSES = (
    "schematic",
    "manual",
    "bom",
    "io_list",
    "hardware",
    "cables",
    "terminals",
    "alarms",
    "motor_drive",
    "plc_xml",
    "plc_scl",
    "s5_text",
    "cross_references",
    "photo",
    "revision",
    "manufacturer_facts",
    "package_container",
    "unrelated",
    "unknown",
)


def upgrade() -> None:
    document_class = sa.Enum(*_DOCUMENT_CLASSES, name="engineering_document_class")
    document_class.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "package_assignments",
        sa.Column("relative_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "package_assignments",
        sa.Column(
            "document_class",
            document_class,
            server_default="unknown",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_package_assignments_tenant_package_path",
        "package_assignments",
        ["tenant_id", "package_id", "relative_path"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_package_assignments_tenant_package_path",
        table_name="package_assignments",
    )
    op.drop_column("package_assignments", "document_class")
    op.drop_column("package_assignments", "relative_path")
    sa.Enum(*_DOCUMENT_CLASSES, name="engineering_document_class").drop(
        op.get_bind(), checkfirst=True
    )
