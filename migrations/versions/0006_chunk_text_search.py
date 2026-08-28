"""Index chunk text for lexical search.

The `simple` text search configuration is chosen deliberately over a language
one. `english` would stem "terminating" to "terminat" and strip stop words,
which helps natural-language recall and actively hurts the thing lexical search
is here for: finding an exact identifier, reference number or phrase. It also
assumes a language, and the corpus is multilingual - picking one would be wrong
for most of it. Semantic search already covers meaning; this covers the literal.

Revision ID: 0006_chunk_text_search
Revises: 0005_normalized_artifact
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_chunk_text_search"
down_revision: str | None = "0005_normalized_artifact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE INDEX ix_chunks_text_search ON chunks USING gin (to_tsvector('simple', text))"
        )
    )
    # Supports the exact-substring path, which the full-text index cannot serve
    # because a fragment of a token is not a token.
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.execute(
        sa.text("CREATE INDEX ix_chunks_text_trigram ON chunks USING gin (text gin_trgm_ops)")
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_chunks_text_trigram"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_chunks_text_search"))
    # The extension is left in place: other things may rely on it, and dropping
    # a shared extension is not this revision's business.
