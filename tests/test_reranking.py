"""Optional second-stage reranking over semantic search.

Reranking must reorder what the first stage found, never change response
shapes, and never turn a working search into an outage.
"""

import uuid

import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.models import Chunk, Document, DocumentStatus
from app.services.indexing import index_document
from app.services.retrieval import search
from app.services.vector_store import VectorStoreService
from tests.test_indexing import _FakeEmbeddings

TEXTS = [
    "Payment is due within thirty days.",
    "The payment schedule follows delivery.",
    "Either party may give notice.",
]


class _PhraseReranker:
    """Deterministic fake: scores a passage high when it contains a phrase."""

    provider = "fake"
    model = "fake-rerank"

    def __init__(self, phrase: str, *, error: Exception | None = None, scores=None) -> None:
        self._phrase = phrase
        self._error = error
        self._scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        self.calls.append((query, list(texts)))
        if self._error is not None:
            raise self._error
        if self._scores is not None:
            return self._scores
        return [1.0 if self._phrase in text.lower() else 0.1 for text in texts]


@pytest_asyncio.fixture
async def corpus(db_session, tenant):
    """One tenant with three indexed single-chunk documents."""
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="test_reranking")
    embeddings = _FakeEmbeddings()

    for index, text in enumerate(TEXTS):
        document = Document(
            tenant_id=tenant.id,
            filename=f"doc-{index}.pdf",
            mime_type="application/pdf",
            storage_key=f"{tenant.id}/{index}",
            file_hash=uuid.uuid4().hex * 2,
            status=DocumentStatus.ready,
        )
        db_session.add(document)
        await db_session.flush()
        db_session.add(
            Chunk(
                tenant_id=tenant.id,
                document_id=document.id,
                ordinal=0,
                text=text,
                source_id=f"{document.id}:00000",
            )
        )
        await db_session.flush()
        await index_document(
            db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
        )

    yield db_session, embeddings, store, tenant
    await client.close()


async def test_without_a_reranker_the_vector_ranking_stands(corpus):
    session, embeddings, store, tenant = corpus

    hits = await search(session, embeddings, store, tenant_id=tenant.id, query="payment", limit=1)

    assert len(hits) == 1
    assert "payment" in hits[0].text.lower()


async def test_reranker_reorders_a_wider_candidate_set(corpus):
    session, embeddings, store, tenant = corpus
    reranker = _PhraseReranker("notice")

    hits = await search(
        session,
        embeddings,
        store,
        tenant_id=tenant.id,
        query="payment",
        limit=1,
        reranker=reranker,
    )

    # The reranker saw more candidates than the caller's limit...
    assert len(reranker.calls) == 1
    assert len(reranker.calls[0][1]) == len(TEXTS)
    # ...but the response still honors the limit, now led by the passage the
    # reranker preferred rather than the closest vector.
    assert len(hits) == 1
    assert "notice" in hits[0].text.lower()
    assert hits[0].score == 1.0


async def test_a_failing_reranker_degrades_to_the_vector_ranking(corpus):
    session, embeddings, store, tenant = corpus
    reranker = _PhraseReranker("notice", error=RuntimeError("endpoint down"))

    hits = await search(
        session,
        embeddings,
        store,
        tenant_id=tenant.id,
        query="payment",
        limit=1,
        reranker=reranker,
    )

    assert len(hits) == 1
    assert "payment" in hits[0].text.lower()


async def test_a_mismatched_score_count_degrades_to_the_vector_ranking(corpus):
    session, embeddings, store, tenant = corpus
    reranker = _PhraseReranker("notice", scores=[0.5])

    hits = await search(
        session,
        embeddings,
        store,
        tenant_id=tenant.id,
        query="payment",
        limit=2,
        reranker=reranker,
    )

    assert len(hits) == 2
    assert all("payment" in hit.text.lower() for hit in hits)


async def test_reranking_never_crosses_the_tenant_boundary(corpus, other_tenant):
    session, embeddings, store, _ = corpus
    reranker = _PhraseReranker("notice")

    hits = await search(
        session,
        embeddings,
        store,
        tenant_id=other_tenant.id,
        query="payment",
        limit=5,
        reranker=reranker,
    )

    assert hits == []
    # Nothing retrieved means nothing is sent to the reranker at all.
    assert reranker.calls == []
