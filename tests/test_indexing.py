"""Indexing and semantic search, end to end.

Real PostgreSQL for the durable side, qdrant-client's own local implementation
for the vector side, and a deterministic fake embedding provider so the tests
assert on ranking rather than on an external model's opinions.
"""

import uuid

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.models import Chunk
from app.services.indexing import IndexingError, index_document
from app.services.retrieval import search
from app.services.vector_store import VectorStoreService

DIMENSIONS = 8


class _FakeEmbeddings:
    """Embeds by keyword presence, so similarity is predictable.

    Each of the first dimensions stands for one keyword; a text scores on the
    ones it contains. Two texts sharing keywords come out close together.
    """

    KEYWORDS = ("payment", "termination", "liability", "notice")

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._error = error

    provider = "fake"
    model = "fake-embed"
    version = "v1"
    dimensions = DIMENSIONS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._error:
            raise self._error
        vectors = []
        for text in texts:
            lowered = text.lower()
            vector = [1.0 if word in lowered else 0.0 for word in self.KEYWORDS]
            vector += [0.0] * (DIMENSIONS - len(self.KEYWORDS))
            if not any(vector):
                vector[-1] = 1.0
            vectors.append(vector)
        return vectors


@pytest_asyncio.fixture
async def store():
    client = AsyncQdrantClient(":memory:")
    service = VectorStoreService(client=client, collection="test_index")
    yield service
    await client.close()


@pytest.fixture
def embeddings():
    return _FakeEmbeddings()


@pytest_asyncio.fixture
def add_chunks(db_session):
    async def _add(document, tenant, texts: list[str]) -> list[Chunk]:
        chunks = []
        for ordinal, text in enumerate(texts):
            chunk = Chunk(
                tenant_id=tenant.id,
                document_id=document.id,
                ordinal=ordinal,
                text=text,
                source_id=f"{document.id}:{ordinal:05d}",
                page_number=ordinal + 1,
                section_title=f"Clause {ordinal + 1}",
            )
            db_session.add(chunk)
            chunks.append(chunk)
        await db_session.flush()
        return chunks

    return _add


async def test_indexing_records_which_provider_made_the_vectors(
    db_session, store, embeddings, tenant, make_document, add_chunks
):
    """A later provider change has to be detectable, not silently mixed in."""
    document = await make_document(tenant)
    await add_chunks(document, tenant, ["payment terms", "termination clause"])

    outcome = await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    assert outcome.indexed == 2
    assert document.embedding_provider == "fake"
    assert document.embedding_model == "fake-embed"
    assert document.embedding_version == "v1"


async def test_search_finds_the_chunk_that_answers_the_query(
    db_session, store, embeddings, tenant, make_document, add_chunks
):
    document = await make_document(tenant)
    await add_chunks(
        document,
        tenant,
        [
            "Payment is due within thirty days.",
            "Either party may give notice of termination.",
        ],
    )
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    hits = await search(
        db_session, embeddings, store, tenant_id=tenant.id, query="payment", limit=5
    )

    assert hits
    assert "Payment is due" in hits[0].text
    assert hits[0].score >= hits[-1].score


async def test_results_carry_the_provenance_from_the_durable_store(
    db_session, store, embeddings, tenant, make_document, add_chunks
):
    document = await make_document(tenant)
    await add_chunks(document, tenant, ["Payment is due within thirty days."])
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    (hit,) = await search(
        db_session, embeddings, store, tenant_id=tenant.id, query="payment", limit=5
    )

    assert hit.page_number == 1
    assert hit.section_title == "Clause 1"
    assert hit.source_id == f"{document.id}:00000"
    assert hit.document_filename == "contract.pdf"


async def test_another_tenant_can_never_retrieve_the_chunks(
    db_session, store, embeddings, tenant, other_tenant, make_document, add_chunks
):
    document = await make_document(tenant)
    await add_chunks(document, tenant, ["Payment is due within thirty days."])
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    hits = await search(
        db_session, embeddings, store, tenant_id=other_tenant.id, query="payment", limit=5
    )

    assert hits == []


async def test_naming_another_tenants_document_returns_nothing(
    db_session, store, embeddings, tenant, other_tenant, make_document, add_chunks
):
    """A document filter narrows; it can never reach across the tenant filter."""
    theirs = await make_document(other_tenant)
    await add_chunks(theirs, other_tenant, ["Payment is due within thirty days."])
    await index_document(
        db_session, embeddings, store, tenant_id=other_tenant.id, document_id=theirs.id
    )

    hits = await search(
        db_session,
        embeddings,
        store,
        tenant_id=tenant.id,
        query="payment",
        limit=5,
        document_ids=[theirs.id],
    )

    assert hits == []


async def test_search_can_be_narrowed_to_one_document(
    db_session, store, embeddings, tenant, make_document, add_chunks
):
    first = await make_document(tenant, file_hash="a" * 64)
    second = await make_document(tenant, file_hash="b" * 64)
    await add_chunks(first, tenant, ["Payment is due within thirty days."])
    await add_chunks(second, tenant, ["Payment terms are negotiable."])
    for document in (first, second):
        await index_document(
            db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
        )

    hits = await search(
        db_session,
        embeddings,
        store,
        tenant_id=tenant.id,
        query="payment",
        limit=10,
        document_ids=[second.id],
    )

    assert {hit.document_id for hit in hits} == {second.id}


async def test_reindexing_does_not_duplicate_results(
    db_session, store, embeddings, tenant, make_document, add_chunks
):
    document = await make_document(tenant)
    await add_chunks(document, tenant, ["Payment is due within thirty days."])

    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    hits = await search(
        db_session, embeddings, store, tenant_id=tenant.id, query="payment", limit=50
    )

    assert len(hits) == 1


async def test_a_document_that_now_has_no_chunks_stops_answering(
    db_session, store, embeddings, tenant, make_document, add_chunks
):
    from sqlalchemy import delete

    document = await make_document(tenant)
    await add_chunks(document, tenant, ["Payment is due within thirty days."])
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    await db_session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    hits = await search(
        db_session, embeddings, store, tenant_id=tenant.id, query="payment", limit=5
    )
    assert hits == []


async def test_a_deleted_chunk_is_not_disclosed_even_if_the_index_lags(
    db_session, store, embeddings, tenant, make_document, add_chunks
):
    """The index having an id is not on its own a reason to return a row."""
    from sqlalchemy import delete

    document = await make_document(tenant)
    await add_chunks(document, tenant, ["Payment is due within thirty days."])
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    # Remove the durable row without touching the index.
    await db_session.execute(delete(Chunk).where(Chunk.document_id == document.id))

    hits = await search(
        db_session, embeddings, store, tenant_id=tenant.id, query="payment", limit=5
    )

    assert hits == []


async def test_an_embedding_failure_is_retryable_and_quotes_nothing(
    db_session, store, tenant, make_document, add_chunks
):
    document = await make_document(tenant)
    await add_chunks(document, tenant, ["CONFIDENTIAL payment terms"])
    failing = _FakeEmbeddings(error=RuntimeError("CONFIDENTIAL payment terms"))

    with pytest.raises(IndexingError) as caught:
        await index_document(
            db_session, failing, store, tenant_id=tenant.id, document_id=document.id
        )

    assert "CONFIDENTIAL" not in str(caught.value)


async def test_indexing_a_missing_document_is_an_error(db_session, store, embeddings, tenant):
    with pytest.raises(IndexingError):
        await index_document(
            db_session, embeddings, store, tenant_id=tenant.id, document_id=uuid.uuid4()
        )


async def test_an_empty_query_asks_the_provider_nothing(db_session, store, embeddings, tenant):
    assert (
        await search(db_session, embeddings, store, tenant_id=tenant.id, query="   ", limit=5) == []
    )
    assert embeddings.calls == []
