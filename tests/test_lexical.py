"""Lexical search against real PostgreSQL.

The point of this path is the literal: an invoice number, a clause reference, a
phrase someone remembers verbatim. These tests are mostly about the cases where
semantic search would shrug.
"""

import pytest
import pytest_asyncio

from app.models import Chunk
from app.services import lexical

TEXTS = [
    "Payment is due within thirty days of invoice INV-2024-0042.",
    "Either party may terminate this agreement with three months notice.",
    "The reference INV-2024-0042/rev2 supersedes the original invoice.",
    "Liability is capped at EUR 1,000,000 per incident.",
]


@pytest_asyncio.fixture
async def corpus(db_session, tenant, make_document):
    document = await make_document(tenant)
    for ordinal, text in enumerate(TEXTS):
        db_session.add(
            Chunk(
                tenant_id=tenant.id,
                document_id=document.id,
                ordinal=ordinal,
                text=text,
                source_id=f"{document.id}:{ordinal:05d}",
                page_number=ordinal + 1,
                section_title=f"Clause {ordinal + 1}",
            )
        )
    await db_session.flush()
    return document


async def test_an_exact_identifier_is_found(db_session, tenant, corpus):
    hits = await lexical.search(db_session, tenant_id=tenant.id, query="INV-2024-0042", limit=10)

    assert hits
    assert any("INV-2024-0042." in hit.text for hit in hits)


async def test_an_identifier_embedded_in_a_longer_token_is_found(db_session, tenant, corpus):
    """`INV-2024-0042/rev2` is one token; a tokeniser will not split it out."""
    hits = await lexical.search(
        db_session, tenant_id=tenant.id, query="INV-2024-0042/rev2", limit=10
    )

    assert [hit.ordinal for hit in hits] == [2]


async def test_a_phrase_is_found(db_session, tenant, corpus):
    hits = await lexical.search(
        db_session, tenant_id=tenant.id, query="three months notice", limit=10
    )

    assert hits
    assert "terminate this agreement" in hits[0].text


async def test_a_word_that_appears_nowhere_returns_nothing(db_session, tenant, corpus):
    assert (
        await lexical.search(db_session, tenant_id=tenant.id, query="arbitration", limit=10) == []
    )


async def test_results_carry_their_provenance(db_session, tenant, corpus):
    (hit,) = await lexical.search(db_session, tenant_id=tenant.id, query="Liability", limit=10)

    assert hit.page_number == 4
    assert hit.section_title == "Clause 4"
    assert hit.source_id.endswith(":00003")
    assert hit.document_filename == "contract.pdf"


async def test_another_tenant_finds_nothing(db_session, tenant, other_tenant, corpus):
    hits = await lexical.search(
        db_session, tenant_id=other_tenant.id, query="INV-2024-0042", limit=10
    )

    assert hits == []


async def test_a_document_filter_narrows_but_never_widens(
    db_session, tenant, other_tenant, corpus, make_document
):
    theirs = await make_document(other_tenant)
    db_session.add(
        Chunk(
            tenant_id=other_tenant.id,
            document_id=theirs.id,
            ordinal=0,
            text="Payment is due within thirty days of invoice INV-2024-0042.",
            source_id=f"{theirs.id}:00000",
        )
    )
    await db_session.flush()

    hits = await lexical.search(
        db_session,
        tenant_id=tenant.id,
        query="INV-2024-0042",
        limit=10,
        document_ids=[theirs.id],
    )

    assert hits == []


async def test_the_limit_is_honoured(db_session, tenant, corpus):
    hits = await lexical.search(db_session, tenant_id=tenant.id, query="invoice", limit=1)

    assert len(hits) == 1


async def test_a_query_of_wildcards_is_treated_as_text(db_session, tenant, corpus):
    """`%` must match a literal percent sign, not every row."""
    assert await lexical.search(db_session, tenant_id=tenant.id, query="%", limit=10) == []


async def test_an_empty_query_returns_nothing(db_session, tenant, corpus):
    assert await lexical.search(db_session, tenant_id=tenant.id, query="  ", limit=10) == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("INV-2024-0042", True),
        ("ABC123", True),
        ("2024", True),
        ("payment terms", False),
        ("liability", False),
        ("", False),
    ],
)
def test_identifier_detection(query, expected):
    assert lexical.looks_like_identifier(query) is expected


async def test_a_source_resolves_to_its_passage(db_session, tenant, corpus):
    hit = await lexical.resolve_source(
        db_session,
        tenant_id=tenant.id,
        document_id=corpus.id,
        source_id=f"{corpus.id}:00001",
    )

    assert hit is not None
    assert "terminate this agreement" in hit.text
    assert hit.page_number == 2


async def test_a_source_does_not_resolve_for_another_tenant(
    db_session, tenant, other_tenant, corpus
):
    hit = await lexical.resolve_source(
        db_session,
        tenant_id=other_tenant.id,
        document_id=corpus.id,
        source_id=f"{corpus.id}:00001",
    )

    assert hit is None


async def test_a_source_does_not_resolve_under_the_wrong_document(
    db_session, tenant, corpus, make_document
):
    other_document = await make_document(tenant, file_hash="f" * 64)

    hit = await lexical.resolve_source(
        db_session,
        tenant_id=tenant.id,
        document_id=other_document.id,
        source_id=f"{corpus.id}:00001",
    )

    assert hit is None
