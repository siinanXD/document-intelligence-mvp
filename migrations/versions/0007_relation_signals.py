"""Widen document relations and record why each was drawn.

A relation without its reasoning is an assertion the reader has to take on
trust. `reason` carries the signals that produced it - which hashes matched,
which entities overlapped, what the vector similarity was - so a relation can
be explained, argued with, and re-derived when a threshold changes.

`version_of` becomes `possible_version`: the detection is a heuristic over
similarity and shared entities, and the name should not claim more certainty
than the evidence supports.

Revision ID: 0007_relation_signals
Revises: 0006_chunk_text_search
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_relation_signals"
down_revision: str | None = "0006_chunk_text_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_VALUES = ("same_case", "same_entity")


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE relation_type RENAME VALUE 'version_of' TO 'possible_version'"))
    for value in NEW_VALUES:
        op.execute(sa.text(f"ALTER TYPE relation_type ADD VALUE IF NOT EXISTS '{value}'"))

    op.add_column(
        "document_relations",
        sa.Column(
            "reason",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("document_relations", "reason")

    # PostgreSQL cannot drop an enum value, so the type is rebuilt without the
    # two that this revision added. Rows using them are not silently discarded:
    # they are removed first, which is the only honest option when the type
    # they depend on is going away.
    op.execute(
        sa.text(
            "DELETE FROM document_relations WHERE relation_type::text IN "
            "('same_case', 'same_entity')"
        )
    )
    op.execute(sa.text("ALTER TYPE relation_type RENAME TO relation_type_old"))
    op.execute(
        sa.text(
            "CREATE TYPE relation_type AS ENUM "
            "('exact_duplicate', 'content_duplicate', 'version_of', 'related')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE document_relations ALTER COLUMN relation_type TYPE relation_type "
            "USING replace(relation_type::text, 'possible_version', 'version_of')"
            "::relation_type"
        )
    )
    op.execute(sa.text("DROP TYPE relation_type_old"))
