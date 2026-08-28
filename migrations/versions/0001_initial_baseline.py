"""Initial baseline.

Establishes the migration chain on an empty database. The MVP tables are
introduced by the data-model work that follows; this revision deliberately
creates nothing so that `alembic upgrade head` is meaningful from day one.

Revision ID: 0001_initial_baseline
Revises:
Create Date: 2026-08-28
"""

from collections.abc import Sequence

revision: str = "0001_initial_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
