"""Record where a document's parsed representation is stored.

Parsing is the expensive step. Keeping its structured output means a later
reindex - a new chunker, a new embedding model - can start from the parsed
document rather than converting the original again.

Revision ID: 0005_normalized_artifact
Revises: 0004_tenant_ownership

The id is kept short on purpose: Alembic stores it in a VARCHAR(32), and a
longer one applies its DDL and then fails to record itself.
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_normalized_artifact"
down_revision: str | None = "0004_tenant_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("normalized_key", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("parser_name", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "parser_name")
    op.drop_column("documents", "normalized_key")
