"""POST /search.

Drives the real route against real PostgreSQL, the local Qdrant implementation
and a deterministic fake embedding provider.
"""

import uuid

import httpx
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.api.dependencies import get_session
from app.main import create_app
from app.models import Chunk, Tenant
from app.services.indexing import index_document
from app.services.vector_store import VectorStoreError, VectorStoreService
from tests.test_indexing import _FakeEmbeddings


@pytest_asyncio.fixture
async def indexed(db_session, monkeypatch):
    """A tenant with two indexed documents, and a client wired to them."""
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="test_api_search")
    embeddings = _FakeEmbeddings()

    tenant = Tenant(slug=f"search-{uuid.uuid4().hex[:8]}", name="Search")
    db_session.add(tenant)
    await db_session.flush()

    from app.models import Document, DocumentStatus

    documents = []
    for index, text in enumerate(
        ["Payment is due within thirty days.", "Either party may give notice."]
    ):
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
                page_number=1,
                section_title="Clause 1",
            )
        )
        await db_session.flush()
        await index_document(
            db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
        )
        documents.append(document)

    monkeypatch.setattr("app.api.search.get_embedding_provider", lambda: embeddings)
    monkeypatch.setattr("app.api.search.VectorStoreService", lambda: store)

    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield http, tenant, documents, store

    await client.close()


async def test_search_returns_the_matching_chunk_with_its_provenance(indexed):
    http, tenant, documents, _ = indexed

    response = await http.post(
        "/search",
        headers={"X-Tenant-Id": str(tenant.id)},
        json={"query": "payment"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    top = results[0]
    assert "Payment is due" in top["text"]
    assert top["page_number"] == 1
    assert top["section_title"] == "Clause 1"
    assert top["source_id"] == f"{documents[0].id}:00000"
    assert top["filename"] == "doc-0.pdf"
    assert 0.0 <= top["score"] <= 1.0


async def test_enabling_the_reranker_keeps_the_response_contract(indexed, monkeypatch):
    """Reranking reorders results; the request and response shapes never change."""
    http, tenant, documents, _ = indexed

    class _PreferNotice:
        provider = "fake"
        model = "fake-rerank"

        async def rerank(self, query: str, texts: list[str]) -> list[float]:
            return [1.0 if "notice" in text.lower() else 0.1 for text in texts]

    monkeypatch.setattr("app.api.search.get_reranker", lambda: _PreferNotice())

    response = await http.post(
        "/search",
        headers={"X-Tenant-Id": str(tenant.id)},
        json={"query": "payment", "limit": 1},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    # The reranker promoted the notice passage over the closest vector.
    assert "notice" in results[0]["text"].lower()
    # Same fields as an unreranked response.
    assert {
        "chunk_id",
        "document_id",
        "filename",
        "source_id",
        "text",
        "score",
        "ordinal",
        "page_number",
        "section_title",
        "source_metadata",
    } <= set(results[0])


async def test_search_can_be_narrowed_to_one_document(indexed):
    http, tenant, documents, _ = indexed

    response = await http.post(
        "/search",
        headers={"X-Tenant-Id": str(tenant.id)},
        json={"query": "payment", "document_ids": [str(documents[1].id)]},
    )

    assert response.status_code == 200
    ids = {result["document_id"] for result in response.json()["results"]}
    assert ids <= {str(documents[1].id)}


async def test_another_tenant_gets_no_results(indexed, db_session):
    http, _, _, _ = indexed
    other = Tenant(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
    db_session.add(other)
    await db_session.flush()

    response = await http.post(
        "/search", headers={"X-Tenant-Id": str(other.id)}, json={"query": "payment"}
    )

    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_the_limit_is_capped_by_configuration(indexed, monkeypatch):
    http, tenant, _, _ = indexed
    monkeypatch.setenv("SEARCH_MAX_LIMIT", "1")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    response = await http.post(
        "/search",
        headers={"X-Tenant-Id": str(tenant.id)},
        json={"query": "payment", "limit": 50},
    )

    assert response.status_code == 200
    assert len(response.json()["results"]) <= 1


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"query": ""}, 422),
        ({"query": "payment", "limit": 0}, 422),
        ({"query": "payment", "document_ids": ["not-a-uuid"]}, 422),
        ({}, 422),
    ],
)
async def test_a_malformed_request_is_refused(indexed, payload, expected):
    http, tenant, _, _ = indexed

    response = await http.post("/search", headers={"X-Tenant-Id": str(tenant.id)}, json=payload)

    assert response.status_code == expected


async def test_the_tenant_header_is_required(indexed):
    http, _, _, _ = indexed

    response = await http.post("/search", json={"query": "payment"})

    assert response.status_code == 400


async def test_a_vector_store_failure_is_a_503_and_says_nothing_more(indexed, monkeypatch):
    """An outage is an outage; the response must not describe the backend."""
    http, tenant, _, store = indexed

    async def _explode(**kwargs):
        raise VectorStoreError("search failed (ConnectionError)")

    monkeypatch.setattr(store, "search", _explode)

    response = await http.post(
        "/search", headers={"X-Tenant-Id": str(tenant.id)}, json={"query": "payment"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "search is temporarily unavailable"
    assert "Connection" not in response.text
