"""Record the dimensionality of the vectors stored for a document.

Provider, model and version already live on the row so a later change is
visible. Dimensions belong with them: two models can share a name-shaped
identifier while producing incompatible vector spaces, and mixing those in one
Qdrant collection corrupts retrieval rather than failing closed.

Revision ID: 0008_embedding_dim
Revises: 0007_relation_signals

The id is kept short on purpose: Alembic stores it in a VARCHAR(32).
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_embedding_dim"
down_revision: str | None = "0007_relation_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("embedding_dimensions", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_documents_embedding_dimensions_positive",
        "documents",
        "embedding_dimensions IS NULL OR embedding_dimensions > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_embedding_dimensions_positive", "documents", type_="check")
    op.drop_column("documents", "embedding_dimensions")
