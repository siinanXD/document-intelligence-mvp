"""Rename document status `pending` to `queued`.

A document is queued for ingestion the moment it is uploaded, and the job that
does that work is queued too. Two words for one state invites the two from
drifting apart, so the document lifecycle borrows the queue's vocabulary.

Revision ID: 0003_document_status_queued
Revises: 0002_document_data_model
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_document_status_queued"
down_revision: str | None = "0002_document_data_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The column default is an expression referring to the label, so it is
    # re-stated after the rename rather than assumed to follow along.
    op.execute(sa.text("ALTER TABLE documents ALTER COLUMN status DROP DEFAULT"))
    op.execute(sa.text("ALTER TYPE document_status RENAME VALUE 'pending' TO 'queued'"))
    op.execute(
        sa.text("ALTER TABLE documents ALTER COLUMN status SET DEFAULT 'queued'::document_status")
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE documents ALTER COLUMN status DROP DEFAULT"))
    op.execute(sa.text("ALTER TYPE document_status RENAME VALUE 'queued' TO 'pending'"))
    op.execute(
        sa.text("ALTER TABLE documents ALTER COLUMN status SET DEFAULT 'pending'::document_status")
    )
