"""Lexical search: finding the literal, not the similar.

Deliberately a separate service from semantic retrieval rather than a mode of
it. They answer different questions - "which passage means this" against "which
passage contains this" - and keeping them apart is what lets a hybrid ranker be
added later without unpicking either.

Both the full-text and the substring path are used. Full text finds tokens and
ranks them; substring finds a reference number embedded inside a longer token,
which no tokeniser will split out. An identifier query must not fail because
the corpus wrote it as `INV-2024-001/rev2`.
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import Float, and_, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document

logger = logging.getLogger(__name__)

# `simple` does no stemming and drops no stop words: an identifier survives it
# intact, and it assumes no language for a multilingual corpus.
TEXT_SEARCH_CONFIG = "simple"

_LOOKS_LIKE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\d[A-Za-z0-9._/-]*")


@dataclass(frozen=True)
class LexicalHit:
    chunk_id: object
    document_id: object
    document_filename: str
    source_id: str
    text: str
    rank: float
    ordinal: int
    page_number: int | None
    section_title: str | None
    source_metadata: dict


def looks_like_identifier(query: str) -> bool:
    """Whether the query is the kind of token people expect to match exactly."""
    stripped = query.strip()
    return bool(stripped) and bool(_LOOKS_LIKE_IDENTIFIER.fullmatch(stripped))


async def search(
    session: AsyncSession,
    *,
    tenant_id,
    query: str,
    limit: int,
    document_ids: list | None = None,
) -> list[LexicalHit]:
    """Return chunks containing the query, most relevant first.

    Scoped to one tenant, always. `document_ids` narrows further and cannot
    widen: the tenant predicate is applied regardless of what is passed.
    """
    query = query.strip()
    if not query:
        return []

    # The configuration argument is a regconfig, not a string: without the cast
    # PostgreSQL finds no matching function. It also has to render the same way
    # the index expression does, or the index will not be used.
    config = cast(literal(TEXT_SEARCH_CONFIG), REGCONFIG)
    tsquery = func.plainto_tsquery(config, query)
    tsvector = func.to_tsvector(config, Chunk.text)
    rank = func.ts_rank_cd(tsvector, tsquery)

    matches_tokens = tsvector.op("@@")(tsquery)
    # autoescape, so a query of "%" is a percent sign rather than a wildcard
    # matching every chunk the tenant owns. ilike() does no escaping of its own.
    matches_substring = Chunk.text.contains(query, autoescape=True)

    conditions = [Chunk.tenant_id == tenant_id, Document.deleted_at.is_(None)]
    if document_ids:
        conditions.append(Chunk.document_id.in_(document_ids))

    statement = (
        select(
            Chunk,
            Document.filename,
            # A substring hit that the tokeniser missed still deserves a rank;
            # ts_rank_cd returns 0 for it, so give it a floor above nothing.
            func.greatest(rank, literal(0.0001, Float)).label("rank"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(and_(*conditions, or_(matches_tokens, matches_substring)))
        .order_by(func.greatest(rank, literal(0.0001, Float)).desc(), Chunk.ordinal)
        .limit(limit)
    )

    rows = (await session.execute(statement)).all()

    return [
        LexicalHit(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_filename=filename,
            source_id=chunk.source_id,
            text=chunk.text,
            rank=float(rank_value),
            ordinal=chunk.ordinal,
            page_number=chunk.page_number,
            section_title=chunk.section_title,
            source_metadata=chunk.source_metadata or {},
        )
        for chunk, filename, rank_value in rows
    ]


async def resolve_source(
    session: AsyncSession, *, tenant_id, document_id, source_id: str
) -> LexicalHit | None:
    """Resolve a citation back to the passage it points at.

    Scoped to the tenant and the document: a source id from another tenant, or
    from another document of the same tenant, resolves to nothing rather than
    to someone else's text.
    """
    row = (
        await session.execute(
            select(Chunk, Document.filename)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.tenant_id == tenant_id,
                Chunk.document_id == document_id,
                Chunk.source_id == source_id,
                Document.deleted_at.is_(None),
            )
        )
    ).first()
    if row is None:
        return None

    chunk, filename = row
    return LexicalHit(
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_filename=filename,
        source_id=chunk.source_id,
        text=chunk.text,
        rank=1.0,
        ordinal=chunk.ordinal,
        page_number=chunk.page_number,
        section_title=chunk.section_title,
        source_metadata=chunk.source_metadata or {},
    )
