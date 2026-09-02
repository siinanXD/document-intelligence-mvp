"""Rebuild vectors from stored chunks, without re-uploading or re-parsing."""

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.models import Chunk, DocumentStatus
from app.services.indexing import index_document, is_current_embedding
from app.services.reindexing import (
    NoStoredChunks,
    ReindexError,
    ReindexNotFound,
    reindex_document,
    reindex_tenant,
)
from app.services.retrieval import search
from app.services.vector_store import DocumentVectorStore, VectorStoreError, VectorStoreService
from tests.test_indexing import DIMENSIONS, _FakeEmbeddings


@pytest_asyncio.fixture
async def stores():
    client = AsyncQdrantClient(":memory:")
    chunks = VectorStoreService(client=client, collection="test_reindex_chunks")
    documents = DocumentVectorStore(client=client, collection="test_reindex_documents")
    yield chunks, documents
    await client.close()


async def _ready_document(db_session, tenant, make_document, texts: list[str], *, file_hash=None):
    document = await make_document(tenant, status=DocumentStatus.ready, file_hash=file_hash)
    for ordinal, text in enumerate(texts):
        db_session.add(
            Chunk(
                tenant_id=tenant.id,
                document_id=document.id,
                ordinal=ordinal,
                text=text,
                source_id=f"{document.id}:{ordinal:05d}",
            )
        )
    await db_session.flush()
    return document


async def test_reindex_rebuilds_from_stored_chunks_without_new_bytes(
    db_session, stores, tenant, make_document
):
    chunk_store, document_store = stores
    embeddings = _FakeEmbeddings()
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    await index_document(
        db_session, embeddings, chunk_store, tenant_id=tenant.id, document_id=document.id
    )

    # Points gone, chunks remain: the original upload is not needed.
    await chunk_store.delete_document(tenant_id=tenant.id, document_id=document.id)
    assert (
        await search(
            db_session, embeddings, chunk_store, tenant_id=tenant.id, query="payment", limit=5
        )
        == []
    )

    outcome = await reindex_document(
        db_session,
        embeddings,
        chunk_store,
        tenant_id=tenant.id,
        document_id=document.id,
        document_vectors=document_store,
    )

    assert outcome.indexed == 1
    assert embeddings.calls[-1] == ["Payment is due within thirty days."]
    hits = await search(
        db_session, embeddings, chunk_store, tenant_id=tenant.id, query="payment", limit=5
    )
    assert len(hits) == 1
    assert hits[0].document_id == document.id


async def test_reindex_replaces_stale_points_instead_of_duplicating_them(
    db_session, stores, tenant, make_document
):
    chunk_store, document_store = stores
    embeddings = _FakeEmbeddings()
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    await index_document(
        db_session, embeddings, chunk_store, tenant_id=tenant.id, document_id=document.id
    )
    await reindex_document(
        db_session,
        embeddings,
        chunk_store,
        tenant_id=tenant.id,
        document_id=document.id,
        document_vectors=document_store,
    )

    hits = await search(
        db_session, embeddings, chunk_store, tenant_id=tenant.id, query="payment", limit=50
    )
    assert len(hits) == 1


async def test_reindex_records_a_new_embedding_identity(db_session, stores, tenant, make_document):
    chunk_store, _ = stores
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    await index_document(
        db_session, _FakeEmbeddings(), chunk_store, tenant_id=tenant.id, document_id=document.id
    )

    class _V2(_FakeEmbeddings):
        version = "v2"

    later = _V2()
    outcome = await reindex_document(
        db_session, later, chunk_store, tenant_id=tenant.id, document_id=document.id
    )

    assert outcome.stale_before is True
    assert is_current_embedding(document, later) is True
    assert document.embedding_version == "v2"


async def test_reindex_of_another_tenants_document_is_absent(
    db_session, stores, tenant, other_tenant, make_document
):
    chunk_store, _ = stores
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    await index_document(
        db_session, _FakeEmbeddings(), chunk_store, tenant_id=tenant.id, document_id=document.id
    )

    with pytest.raises(ReindexNotFound):
        await reindex_document(
            db_session,
            _FakeEmbeddings(),
            chunk_store,
            tenant_id=other_tenant.id,
            document_id=document.id,
        )


async def test_reindex_without_chunks_is_an_error(db_session, stores, tenant, make_document):
    chunk_store, _ = stores
    document = await make_document(tenant, status=DocumentStatus.ready)

    with pytest.raises(NoStoredChunks, match="no stored chunks"):
        await reindex_document(
            db_session, _FakeEmbeddings(), chunk_store, tenant_id=tenant.id, document_id=document.id
        )


async def test_tenant_reindex_rebuilds_only_that_tenants_documents(
    db_session, stores, tenant, other_tenant, make_document
):
    chunk_store, document_store = stores
    embeddings = _FakeEmbeddings()
    mine = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    theirs = await _ready_document(
        db_session,
        other_tenant,
        make_document,
        ["Payment is due within thirty days."],
        file_hash="c" * 64,
    )
    empty = await make_document(tenant, status=DocumentStatus.ready, file_hash="d" * 64)
    for document, owner in ((mine, tenant), (theirs, other_tenant)):
        await index_document(
            db_session, embeddings, chunk_store, tenant_id=owner.id, document_id=document.id
        )

    await chunk_store.delete_document(tenant_id=tenant.id, document_id=mine.id)

    outcome = await reindex_tenant(
        db_session,
        embeddings,
        chunk_store,
        tenant_id=tenant.id,
        document_vectors=document_store,
    )

    assert outcome.reindexed == 1
    assert outcome.skipped == 1
    assert outcome.failed == 0
    assert empty.status is DocumentStatus.ready

    mine_hits = await search(
        db_session, embeddings, chunk_store, tenant_id=tenant.id, query="payment", limit=5
    )
    their_hits = await search(
        db_session, embeddings, chunk_store, tenant_id=other_tenant.id, query="payment", limit=5
    )
    assert [hit.document_id for hit in mine_hits] == [mine.id]
    assert [hit.document_id for hit in their_hits] == [theirs.id]


async def test_tenant_reindex_counts_a_provider_outage_as_failed(
    db_session, stores, tenant, make_document
):
    chunk_store, document_store = stores
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    await index_document(
        db_session, _FakeEmbeddings(), chunk_store, tenant_id=tenant.id, document_id=document.id
    )

    class _Down(_FakeEmbeddings):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("APIError")

    outcome = await reindex_tenant(
        db_session,
        _Down(),
        chunk_store,
        tenant_id=tenant.id,
        document_vectors=document_store,
    )

    assert outcome.reindexed == 0
    assert outcome.skipped == 0
    assert outcome.failed == 1


async def test_reindex_surfaces_a_provider_outage_as_reindex_error(
    db_session, stores, tenant, make_document
):
    chunk_store, _ = stores
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    await index_document(
        db_session, _FakeEmbeddings(), chunk_store, tenant_id=tenant.id, document_id=document.id
    )

    class _Down(_FakeEmbeddings):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("APIError")

    with pytest.raises(ReindexError, match="embedding failed"):
        await reindex_document(
            db_session, _Down(), chunk_store, tenant_id=tenant.id, document_id=document.id
        )


class _FakeHuggingFaceEmbeddings(_FakeEmbeddings):
    """The same deterministic vectors under a different provider identity."""

    provider = "huggingface"
    model = "BAAI/bge-m3"


async def test_switching_the_embedding_provider_needs_only_a_reindex(
    db_session, stores, tenant, make_document
):
    """SIN-71: OpenAI -> Hugging Face is a configuration change plus a reindex."""
    chunk_store, _ = stores
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    old = _FakeEmbeddings()
    await index_document(db_session, old, chunk_store, tenant_id=tenant.id, document_id=document.id)

    new = _FakeHuggingFaceEmbeddings()
    # Before the reindex, the stale points are dropped rather than returned as
    # if they lived in the new provider's embedding space.
    assert is_current_embedding(document, new) is False
    assert (
        await search(db_session, new, chunk_store, tenant_id=tenant.id, query="payment", limit=5)
        == []
    )

    outcome = await reindex_document(
        db_session, new, chunk_store, tenant_id=tenant.id, document_id=document.id
    )

    assert outcome.stale_before is True
    assert document.embedding_provider == "huggingface"
    assert document.embedding_model == "BAAI/bge-m3"
    hits = await search(db_session, new, chunk_store, tenant_id=tenant.id, query="payment", limit=5)
    assert [hit.document_id for hit in hits] == [document.id]


async def test_a_dimension_change_fails_closed_before_any_write(
    db_session, stores, tenant, make_document
):
    """A provider with a different width must not touch the old collection."""
    chunk_store, _ = stores
    document = await _ready_document(
        db_session, tenant, make_document, ["Payment is due within thirty days."]
    )
    old = _FakeEmbeddings()
    await index_document(db_session, old, chunk_store, tenant_id=tenant.id, document_id=document.id)

    class _Wider(_FakeHuggingFaceEmbeddings):
        dimensions = DIMENSIONS * 2

    with pytest.raises(VectorStoreError, match="dimensions"):
        await reindex_document(
            db_session, _Wider(), chunk_store, tenant_id=tenant.id, document_id=document.id
        )

    # The existing vectors survived and still answer for the old provider.
    hits = await search(db_session, old, chunk_store, tenant_id=tenant.id, query="payment", limit=5)
    assert [hit.document_id for hit in hits] == [document.id]
