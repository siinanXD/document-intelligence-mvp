"""The Qdrant seam, against the client's real local implementation.

`AsyncQdrantClient(":memory:")` is not a stub written for these tests: it is
qdrant-client's own in-process implementation, so collection handling, payload
filtering and cosine scoring are the library's, not a fake's. What it does not
exercise is the server's HNSW index - local mode searches exhaustively - so
these tests prove correctness and filtering, not recall or performance.
"""

import uuid

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.services.vector_store import VectorStoreService, bounded_similarity

DIMENSIONS = 4


@pytest_asyncio.fixture
async def store():
    client = AsyncQdrantClient(":memory:")
    service = VectorStoreService(client=client, collection="test_chunks")
    await service.ensure_collection(dimensions=DIMENSIONS)
    yield service
    await client.close()


def _point(vector, source_id="s"):
    return (uuid.uuid4(), vector, source_id)


async def test_ensure_collection_is_idempotent(store):
    """Called on every index; the second call must not recreate anything."""
    assert await store.ensure_collection(dimensions=DIMENSIONS) is False


async def test_chunks_come_back_ranked_by_similarity(store):
    tenant, document = uuid.uuid4(), uuid.uuid4()
    near = _point([1.0, 0.0, 0.0, 0.0], "near")
    far = _point([0.0, 1.0, 0.0, 0.0], "far")
    await store.upsert_chunks(tenant_id=tenant, document_id=document, points=[far, near])

    results = await store.search(tenant_id=tenant, vector=[1.0, 0.0, 0.0, 0.0], limit=5)

    assert [chunk_id for chunk_id, _ in results][0] == near[0]
    assert results[0][1] > results[1][1]


async def test_search_keeps_negative_cosine_order(store):
    """Flooring negatives to 0.0 would make opposite and orthogonal chunks tie."""
    tenant, document = uuid.uuid4(), uuid.uuid4()
    opposite = _point([-1.0, 0.0, 0.0, 0.0], "opposite")
    orthogonal = _point([0.0, 1.0, 0.0, 0.0], "orthogonal")
    await store.upsert_chunks(tenant_id=tenant, document_id=document, points=[opposite, orthogonal])

    results = await store.search(tenant_id=tenant, vector=[1.0, 0.0, 0.0, 0.0], limit=5)
    scores = dict(results)

    assert scores[orthogonal[0]] > scores[opposite[0]]
    assert scores[opposite[0]] < 0.0
    assert bounded_similarity(scores[opposite[0]]) == scores[opposite[0]]


async def test_another_tenant_cannot_see_the_points(store):
    mine, theirs, document = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await store.upsert_chunks(
        tenant_id=mine, document_id=document, points=[_point([1.0, 0.0, 0.0, 0.0])]
    )

    assert await store.search(tenant_id=theirs, vector=[1.0, 0.0, 0.0, 0.0], limit=5) == []


async def test_a_document_filter_narrows_but_never_widens(store):
    tenant = uuid.uuid4()
    wanted, other = uuid.uuid4(), uuid.uuid4()
    kept = _point([1.0, 0.0, 0.0, 0.0])
    await store.upsert_chunks(tenant_id=tenant, document_id=wanted, points=[kept])
    await store.upsert_chunks(
        tenant_id=tenant, document_id=other, points=[_point([1.0, 0.0, 0.0, 0.0])]
    )

    results = await store.search(
        tenant_id=tenant, vector=[1.0, 0.0, 0.0, 0.0], limit=5, document_ids=[wanted]
    )

    assert [chunk_id for chunk_id, _ in results] == [kept[0]]


async def test_a_document_id_from_another_tenant_matches_nothing(store):
    """The tenant filter applies regardless of what document ids are passed."""
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    their_document = uuid.uuid4()
    await store.upsert_chunks(
        tenant_id=theirs, document_id=their_document, points=[_point([1.0, 0, 0, 0])]
    )

    results = await store.search(
        tenant_id=mine,
        vector=[1.0, 0.0, 0.0, 0.0],
        limit=5,
        document_ids=[their_document],
    )

    assert results == []


async def test_reindexing_replaces_points_instead_of_duplicating_them(store):
    tenant, document = uuid.uuid4(), uuid.uuid4()
    first = [_point([1.0, 0, 0, 0]), _point([0, 1.0, 0, 0])]
    await store.upsert_chunks(tenant_id=tenant, document_id=document, points=first)

    second = [_point([1.0, 0, 0, 0])]
    await store.upsert_chunks(tenant_id=tenant, document_id=document, points=second)

    results = await store.search(tenant_id=tenant, vector=[1.0, 0, 0, 0], limit=50)

    assert len(results) == 1
    assert results[0][0] == second[0][0]


async def test_reindexing_the_same_chunk_id_does_not_duplicate_it(store):
    tenant, document = uuid.uuid4(), uuid.uuid4()
    chunk_id = uuid.uuid4()
    await store.upsert_chunks(
        tenant_id=tenant, document_id=document, points=[(chunk_id, [1.0, 0, 0, 0], "s")]
    )
    await store.upsert_chunks(
        tenant_id=tenant, document_id=document, points=[(chunk_id, [0, 1.0, 0, 0], "s")]
    )

    results = await store.search(tenant_id=tenant, vector=[0, 1.0, 0, 0], limit=50)

    assert len(results) == 1
    assert results[0][1] == pytest.approx(1.0, abs=1e-6)


async def test_indexing_no_chunks_clears_what_was_there(store):
    tenant, document = uuid.uuid4(), uuid.uuid4()
    await store.upsert_chunks(
        tenant_id=tenant, document_id=document, points=[_point([1.0, 0, 0, 0])]
    )

    assert await store.upsert_chunks(tenant_id=tenant, document_id=document, points=[]) == 0
    assert await store.search(tenant_id=tenant, vector=[1.0, 0, 0, 0], limit=5) == []


async def test_deleting_one_document_leaves_the_others_alone(store):
    tenant = uuid.uuid4()
    kept_document, removed_document = uuid.uuid4(), uuid.uuid4()
    kept = _point([1.0, 0, 0, 0])
    await store.upsert_chunks(tenant_id=tenant, document_id=kept_document, points=[kept])
    await store.upsert_chunks(
        tenant_id=tenant, document_id=removed_document, points=[_point([1.0, 0, 0, 0])]
    )

    await store.delete_document(tenant_id=tenant, document_id=removed_document)

    results = await store.search(tenant_id=tenant, vector=[1.0, 0, 0, 0], limit=50)
    assert [chunk_id for chunk_id, _ in results] == [kept[0]]


async def test_deleting_a_tenant_leaves_the_other_tenants_alone(store):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    kept = _point([1.0, 0, 0, 0])
    await store.upsert_chunks(tenant_id=theirs, document_id=uuid.uuid4(), points=[kept])
    await store.upsert_chunks(
        tenant_id=mine, document_id=uuid.uuid4(), points=[_point([1.0, 0, 0, 0])]
    )

    await store.delete_tenant(tenant_id=mine)

    assert await store.search(tenant_id=mine, vector=[1.0, 0, 0, 0], limit=5) == []
    assert len(await store.search(tenant_id=theirs, vector=[1.0, 0, 0, 0], limit=5)) == 1


async def test_the_payload_carries_identifiers_and_no_text(store):
    """A vector store breach should yield ids, not customer documents."""
    tenant, document = uuid.uuid4(), uuid.uuid4()
    chunk_id = uuid.uuid4()
    await store.upsert_chunks(
        tenant_id=tenant,
        document_id=document,
        points=[(chunk_id, [1.0, 0, 0, 0], "doc:00001")],
    )

    raw = await store._client.query_points(
        collection_name=store.collection, query=[1.0, 0, 0, 0], limit=1, with_payload=True
    )
    payload = raw.points[0].payload

    assert set(payload) == {"tenant_id", "document_id", "chunk_id", "source_id"}
    assert payload["source_id"] == "doc:00001"


async def test_searching_before_anything_is_indexed_is_empty_not_an_error():
    """A tenant's first question must not answer 503 because nothing exists yet."""
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="never_created")

    assert await store.search(tenant_id=uuid.uuid4(), vector=[1.0, 0, 0, 0], limit=5) == []

    await client.close()


async def test_a_failing_query_against_a_live_store_surfaces(store, monkeypatch):
    """Swallowing the missing-collection case must not swallow a query failure."""
    from app.services.vector_store import VectorStoreError

    async def _explode(**kwargs):
        raise ConnectionError("qdrant rejected the query")

    monkeypatch.setattr(store._client, "query_points", _explode)

    with pytest.raises(VectorStoreError):
        await store.search(tenant_id=uuid.uuid4(), vector=[1.0, 0, 0, 0], limit=5)


async def test_a_total_outage_surfaces_rather_than_answering_empty(store, monkeypatch):
    """The case the first version of this code got wrong.

    When the store is wholly unreachable both the query and the
    collection-existence probe fail. Reading that as "the collection is not
    there" returns no results, and a caller told "no results" during an outage
    reports it as a wrong answer - worse than an error, because it looks like
    one. The probe must distinguish "it is not there" from "I could not ask".
    """
    from app.services.vector_store import VectorStoreError

    async def _down(*args, **kwargs):
        raise ConnectionError("qdrant is unreachable")

    monkeypatch.setattr(store._client, "query_points", _down)
    monkeypatch.setattr(store._client, "get_collections", _down)

    with pytest.raises(VectorStoreError):
        await store.search(tenant_id=uuid.uuid4(), vector=[1.0, 0, 0, 0], limit=5)


async def test_a_dimension_mismatch_is_refused_rather_than_mixed(store):
    from app.services.vector_store import VectorStoreError

    with pytest.raises(VectorStoreError, match="dimensions"):
        await store.ensure_collection(dimensions=DIMENSIONS + 4)


async def test_deleting_from_a_missing_collection_is_success():
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="never_created")

    await store.delete_document(tenant_id=uuid.uuid4(), document_id=uuid.uuid4())
    await store.delete_tenant(tenant_id=uuid.uuid4())

    await client.close()
