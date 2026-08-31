"""POST /ask, lexical /search and source resolution, through the real routes."""

import uuid

import httpx
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.api.dependencies import get_session
from app.main import create_app
from app.models import Chunk, Document, DocumentStatus, Tenant
from app.services.indexing import index_document
from app.services.qa import GroundedAnswer
from app.services.vector_store import VectorStoreService
from tests.test_indexing import _FakeEmbeddings
from tests.test_qa import _FakeLLM

TEXTS = [
    "Payment is due within thirty days of invoice INV-2024-0042.",
    "Either party may terminate with three months notice.",
]


@pytest_asyncio.fixture
async def api(db_session, monkeypatch):
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="test_api_ask")
    embeddings = _FakeEmbeddings()
    llm = _FakeLLM()

    tenant = Tenant(slug=f"ask-{uuid.uuid4().hex[:8]}", name="Ask")
    db_session.add(tenant)
    await db_session.flush()

    document = Document(
        tenant_id=tenant.id,
        filename="contract.pdf",
        mime_type="application/pdf",
        storage_key=f"{tenant.id}/doc",
        file_hash=uuid.uuid4().hex * 2,
        status=DocumentStatus.ready,
    )
    db_session.add(document)
    await db_session.flush()

    source_ids = []
    for ordinal, text in enumerate(TEXTS):
        source_id = f"{document.id}:{ordinal:05d}"
        db_session.add(
            Chunk(
                tenant_id=tenant.id,
                document_id=document.id,
                ordinal=ordinal,
                text=text,
                source_id=source_id,
                page_number=ordinal + 1,
                section_title=f"Clause {ordinal + 1}",
            )
        )
        source_ids.append(source_id)
    await db_session.flush()
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    for module in ("app.api.ask", "app.api.search"):
        monkeypatch.setattr(f"{module}.get_embedding_provider", lambda: embeddings)
        monkeypatch.setattr(f"{module}.VectorStoreService", lambda: store)
    monkeypatch.setattr("app.api.ask.get_llm_provider", lambda: llm)

    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield http, tenant, document, source_ids, llm

    await client.close()


def _headers(tenant):
    return {"X-Tenant-Id": str(tenant.id)}


async def test_ask_answers_with_resolvable_sources(api):
    http, tenant, document, source_ids, llm = api
    llm.answer = GroundedAnswer(
        answer="Within thirty days.",
        source_ids=[source_ids[0]],
        has_sufficient_evidence=True,
    )

    response = await http.post(
        "/ask", headers=_headers(tenant), json={"question": "When is payment due?"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_sufficient_evidence"] is True
    assert [source["source_id"] for source in body["sources"]] == [source_ids[0]]
    assert set(body) == {
        "answer",
        "has_sufficient_evidence",
        "conflicting",
        "sources",
        "considered",
    }

    # Every id in the answer resolves through the API, which is the point of it.
    resolved = await http.get(
        f"/documents/{document.id}/sources/{source_ids[0]}", headers=_headers(tenant)
    )
    assert resolved.status_code == 200
    assert "thirty days" in resolved.json()["text"]
    assert resolved.json()["page_number"] == 1


async def test_missing_evidence_is_a_200_that_says_so(api):
    http, tenant, _, _, llm = api
    llm.answer = GroundedAnswer(
        answer="The documents do not state the governing law.",
        source_ids=[],
        has_sufficient_evidence=False,
    )

    response = await http.post(
        "/ask", headers=_headers(tenant), json={"question": "Which law governs?"}
    )

    assert response.status_code == 200
    assert response.json()["has_sufficient_evidence"] is False
    assert response.json()["sources"] == []


async def test_a_conflict_is_surfaced_rather_than_smoothed_over(api):
    http, tenant, _, source_ids, llm = api
    llm.answer = GroundedAnswer(
        answer="The documents disagree.",
        source_ids=source_ids,
        has_sufficient_evidence=True,
        conflicting=True,
    )

    response = await http.post(
        "/ask", headers=_headers(tenant), json={"question": "When is payment due?"}
    )

    body = response.json()
    assert body["conflicting"] is True
    assert len(body["sources"]) == 2


async def test_another_tenant_gets_no_evidence(api, db_session):
    http, _, _, _, _ = api
    other = Tenant(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
    db_session.add(other)
    await db_session.flush()

    response = await http.post(
        "/ask",
        headers={"X-Tenant-Id": str(other.id)},
        json={"question": "When is payment due?"},
    )

    assert response.status_code == 200
    assert response.json()["has_sufficient_evidence"] is False
    assert response.json()["sources"] == []


async def test_lexical_search_finds_an_exact_identifier(api):
    http, tenant, _, _, _ = api

    response = await http.post(
        "/search",
        headers=_headers(tenant),
        json={"query": "INV-2024-0042", "mode": "lexical"},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert "INV-2024-0042" in results[0]["text"]


async def test_lexical_search_needs_no_embedding_provider(api, monkeypatch):
    """PostgreSQL answers it alone, so it survives an AI outage."""
    http, tenant, _, _, _ = api

    def _unavailable():
        raise AssertionError("lexical search must not build an embedding provider")

    monkeypatch.setattr("app.api.search.get_embedding_provider", _unavailable)

    response = await http.post(
        "/search",
        headers=_headers(tenant),
        json={"query": "INV-2024-0042", "mode": "lexical"},
    )

    assert response.status_code == 200


async def test_the_default_mode_is_semantic(api):
    http, tenant, _, _, _ = api

    response = await http.post("/search", headers=_headers(tenant), json={"query": "payment"})

    assert response.status_code == 200
    assert response.json()["results"]


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": "x", "limit": 0},
        {"question": "x", "document_ids": ["nope"]},
        {},
    ],
)
async def test_a_malformed_ask_is_refused(api, payload):
    http, tenant, _, _, _ = api

    response = await http.post("/ask", headers=_headers(tenant), json=payload)

    assert response.status_code == 422


async def test_the_tenant_header_is_required_to_ask(api):
    http, _, _, _, _ = api

    assert (await http.post("/ask", json={"question": "x"})).status_code == 400


async def test_a_source_from_another_tenant_is_absent_not_forbidden(api, db_session):
    http, _, document, source_ids, _ = api
    other = Tenant(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
    db_session.add(other)
    await db_session.flush()

    response = await http.get(
        f"/documents/{document.id}/sources/{source_ids[0]}",
        headers={"X-Tenant-Id": str(other.id)},
    )

    assert response.status_code == 404


async def test_an_unknown_source_is_a_404(api):
    http, tenant, document, _, _ = api

    response = await http.get(
        f"/documents/{document.id}/sources/{document.id}:99999", headers=_headers(tenant)
    )

    assert response.status_code == 404
