"""Cross-tenant negatives at the HTTP boundary.

CLAUDE.md requires every read path to prove another tenant cannot see the
data. These drive the real routes: Tenant B asking about Tenant A's document
must look like absence, never like a permission error that confirms the id.
"""

import uuid

import httpx
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.api.dependencies import get_session, get_storage
from app.main import create_app
from app.models import (
    Chunk,
    Document,
    DocumentProfile,
    DocumentRelation,
    DocumentStatus,
    RelationType,
    Tenant,
)
from app.providers.local_storage import LocalStorageBackend
from app.services.indexing import index_document
from app.services.qa import GroundedAnswer
from app.services.vector_store import DocumentVectorStore, VectorStoreService
from tests.test_indexing import _FakeEmbeddings
from tests.test_qa import _FakeLLM

SECRET = "CONFIDENTIAL payment terms for CASE-ALPHA"


@pytest_asyncio.fixture
async def isolated(db_session, tmp_path, monkeypatch):
    client = AsyncQdrantClient(":memory:")
    chunk_store = VectorStoreService(client=client, collection="test_isolation_chunks")
    document_store = DocumentVectorStore(client=client, collection="test_isolation_docs")
    embeddings = _FakeEmbeddings()
    llm = _FakeLLM(GroundedAnswer(answer="not used", source_ids=[], has_sufficient_evidence=False))
    storage = LocalStorageBackend(tmp_path / "objects")

    owner = Tenant(slug=f"owner-{uuid.uuid4().hex[:8]}", name="Owner")
    outsider = Tenant(slug=f"out-{uuid.uuid4().hex[:8]}", name="Outsider")
    db_session.add_all([owner, outsider])
    await db_session.flush()

    document = Document(
        tenant_id=owner.id,
        filename="secret.pdf",
        mime_type="application/pdf",
        storage_key=f"{owner.id}/doc/secret.pdf",
        normalized_key=f"{owner.id}/doc/secret.pdf.docling.json",
        file_hash="a" * 64,
        status=DocumentStatus.ready,
        title="Secret contract",
    )
    related = Document(
        tenant_id=owner.id,
        filename="related.pdf",
        mime_type="application/pdf",
        storage_key=f"{owner.id}/rel/related.pdf",
        file_hash="b" * 64,
        status=DocumentStatus.ready,
    )
    db_session.add_all([document, related])
    await db_session.flush()

    source_id = f"{document.id}:00000"
    db_session.add(
        Chunk(
            tenant_id=owner.id,
            document_id=document.id,
            ordinal=0,
            text=SECRET,
            source_id=source_id,
            page_number=1,
            section_title="Payment",
        )
    )
    db_session.add(
        DocumentProfile(tenant_id=owner.id, document_id=document.id, identifiers=["CASE-ALPHA"])
    )
    db_session.add(
        DocumentRelation(
            tenant_id=owner.id,
            source_document_id=related.id,
            target_document_id=document.id,
            relation_type=RelationType.same_case,
            score=1.0,
            reason={"signals": ["shared_identifiers"]},
        )
    )
    await db_session.flush()
    await storage.put(document.storage_key, b"%PDF-1.7 secret")
    await storage.put(document.normalized_key, b'{"schema":"fake"}')
    await index_document(
        db_session, embeddings, chunk_store, tenant_id=owner.id, document_id=document.id
    )

    for module in ("app.api.search", "app.api.ask", "app.api.documents"):
        monkeypatch.setattr(f"{module}.get_embedding_provider", lambda: embeddings)
        monkeypatch.setattr(f"{module}.VectorStoreService", lambda: chunk_store)
    monkeypatch.setattr("app.api.ask.get_llm_provider", lambda: llm)
    monkeypatch.setattr("app.api.documents.DocumentVectorStore", lambda: document_store)

    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override
    application.dependency_overrides[get_storage] = lambda: storage

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield {
            "http": http,
            "owner": owner,
            "outsider": outsider,
            "document": document,
            "related": related,
            "source_id": source_id,
            "storage": storage,
            "chunk_store": chunk_store,
        }

    await client.close()


def _headers(tenant):
    return {"X-Tenant-Id": str(tenant.id)}


async def test_another_tenant_cannot_fetch_the_document(isolated):
    http, outsider, document = isolated["http"], isolated["outsider"], isolated["document"]

    response = await http.get(f"/documents/{document.id}", headers=_headers(outsider))

    assert response.status_code == 404


async def test_another_tenant_cannot_read_the_pipeline(isolated):
    response = await isolated["http"].get(
        f"/documents/{isolated['document'].id}/pipeline",
        headers=_headers(isolated["outsider"]),
    )
    assert response.status_code == 404


async def test_another_tenant_cannot_resolve_its_sources(isolated):
    http = isolated["http"]
    response = await http.get(
        f"/documents/{isolated['document'].id}/sources/{isolated['source_id']}",
        headers=_headers(isolated["outsider"]),
    )

    assert response.status_code == 404
    assert SECRET not in response.text


async def test_another_tenant_cannot_retrieve_it_semantically(isolated):
    response = await isolated["http"].post(
        "/search",
        headers=_headers(isolated["outsider"]),
        json={"query": "payment", "mode": "semantic"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_another_tenant_cannot_retrieve_it_lexically(isolated):
    response = await isolated["http"].post(
        "/search",
        headers=_headers(isolated["outsider"]),
        json={"query": "CASE-ALPHA", "mode": "lexical"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_another_tenant_cannot_ask_questions_over_it(isolated):
    response = await isolated["http"].post(
        "/ask",
        headers=_headers(isolated["outsider"]),
        json={"question": "When is payment due?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_sufficient_evidence"] is False
    assert body["sources"] == []
    assert SECRET not in response.text


async def test_another_tenant_cannot_see_relations_to_it(isolated):
    response = await isolated["http"].get(
        f"/documents/{isolated['related'].id}/relations",
        headers=_headers(isolated["outsider"]),
    )

    assert response.status_code == 404


async def test_another_tenant_cannot_delete_it(isolated):
    http, outsider, document, owner = (
        isolated["http"],
        isolated["outsider"],
        isolated["document"],
        isolated["owner"],
    )

    response = await http.delete(f"/documents/{document.id}", headers=_headers(outsider))

    assert response.status_code == 404
    owned = await http.get(f"/documents/{document.id}", headers=_headers(owner))
    assert owned.status_code == 200
    assert await isolated["storage"].exists(document.storage_key) is True


async def test_the_owner_still_sees_their_own_document(isolated):
    response = await isolated["http"].get(
        f"/documents/{isolated['document'].id}", headers=_headers(isolated["owner"])
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(isolated["document"].id)


async def test_the_owner_can_delete_and_the_document_becomes_inaccessible(isolated):
    http, owner, document = isolated["http"], isolated["owner"], isolated["document"]
    headers = _headers(owner)

    response = await http.delete(f"/documents/{document.id}", headers=headers)

    assert response.status_code == 204
    assert (await http.get(f"/documents/{document.id}", headers=headers)).status_code == 404
    assert (
        await http.get(f"/documents/{document.id}/sources/{isolated['source_id']}", headers=headers)
    ).status_code == 404
    assert (await http.post("/search", headers=headers, json={"query": "payment"})).json()[
        "results"
    ] == []
    assert (
        await http.post("/search", headers=headers, json={"query": "CASE-ALPHA", "mode": "lexical"})
    ).json()["results"] == []
    ask = await http.post("/ask", headers=headers, json={"question": "When is payment due?"})
    assert ask.json()["has_sufficient_evidence"] is False
    assert (
        await http.get(f"/documents/{isolated['related'].id}/relations", headers=headers)
    ).json() == []
    assert await isolated["storage"].exists(document.storage_key) is False

    # Repeating the delete is still success.
    assert (await http.delete(f"/documents/{document.id}", headers=headers)).status_code == 204


async def test_the_owner_can_reindex_without_reuploading(isolated):
    http, owner, document = isolated["http"], isolated["owner"], isolated["document"]
    headers = _headers(owner)

    await isolated["chunk_store"].delete_document(tenant_id=owner.id, document_id=document.id)
    assert (await http.post("/search", headers=headers, json={"query": "payment"})).json()[
        "results"
    ] == []

    response = await http.post(f"/documents/{document.id}/reindex", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["indexed"] >= 1
    assert (await http.post("/search", headers=headers, json={"query": "payment"})).json()[
        "results"
    ]

    tenant = await http.post("/reindex", headers=headers)
    assert tenant.status_code == 200
    assert tenant.json()["reindexed"] >= 1


async def test_another_tenant_cannot_reindex_it(isolated):
    response = await isolated["http"].post(
        f"/documents/{isolated['document'].id}/reindex",
        headers=_headers(isolated["outsider"]),
    )

    assert response.status_code == 404
    assert SECRET not in response.text


async def test_a_soft_deleted_document_cannot_be_reindexed(isolated):
    http, owner, document = isolated["http"], isolated["owner"], isolated["document"]
    headers = _headers(owner)
    deleted = await http.delete(f"/documents/{document.id}", headers=headers)
    assert deleted.status_code == 204

    response = await http.post(f"/documents/{document.id}/reindex", headers=headers)

    assert response.status_code == 404
    assert SECRET not in response.text


async def test_reindex_reports_a_provider_outage_as_unavailable(isolated, monkeypatch):
    class _Down(_FakeEmbeddings):
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("APIError")

    monkeypatch.setattr("app.api.documents.get_embedding_provider", lambda: _Down())

    response = await isolated["http"].post(
        f"/documents/{isolated['document'].id}/reindex",
        headers=_headers(isolated["owner"]),
    )

    assert response.status_code == 503
    assert "no stored chunks" not in response.text.lower()
