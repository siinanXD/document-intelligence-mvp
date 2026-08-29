"""GET /documents/{id}/relations."""

import uuid

import httpx
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.api.dependencies import get_session
from app.main import create_app
from app.models import Document, DocumentProfile, DocumentStatus, Tenant
from app.services.relations import detect_relations
from app.services.vector_store import DocumentVectorStore


@pytest_asyncio.fixture
async def api(db_session):
    client = AsyncQdrantClient(":memory:")
    store = DocumentVectorStore(client=client, collection="test_api_relations")
    await store.ensure_collection(dimensions=4)

    tenant = Tenant(slug=f"rel-{uuid.uuid4().hex[:8]}", name="Rel")
    db_session.add(tenant)
    await db_session.flush()

    documents = []
    for index, identifier in enumerate(["CASE-7", "CASE-7"]):
        document = Document(
            tenant_id=tenant.id,
            filename=f"doc-{index}.pdf",
            mime_type="application/pdf",
            storage_key=f"{tenant.id}/{index}",
            file_hash=uuid.uuid4().hex * 2,
            status=DocumentStatus.ready,
            title=f"Document {index}",
            document_type="letter",
        )
        db_session.add(document)
        await db_session.flush()
        db_session.add(
            DocumentProfile(tenant_id=tenant.id, document_id=document.id, identifiers=[identifier])
        )
        await db_session.flush()
        await store.upsert(
            tenant_id=tenant.id, document_id=document.id, vector=[1.0, float(index), 0, 0]
        )
        documents.append(document)

    await detect_relations(
        db_session,
        store,
        tenant_id=tenant.id,
        document_id=documents[1].id,
        vector=[1.0, 1.0, 0, 0],
    )

    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield http, tenant, documents

    await client.close()


async def test_relations_are_listed_with_their_reasons(api):
    http, tenant, documents = api

    response = await http.get(
        f"/documents/{documents[1].id}/relations",
        headers={"X-Tenant-Id": str(tenant.id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["relation_type"] == "same_case"
    # The reason is the point: a relation nobody can interrogate is one nobody
    # will trust.
    assert body[0]["reason"]["signals"] == ["shared_identifiers"]
    assert body[0]["reason"]["shared_identifiers"] == ["CASE-7"]
    assert body[0]["target"]["document_id"] == str(documents[0].id)
    assert body[0]["target"]["title"] == "Document 0"


async def test_the_relation_is_visible_from_the_other_document_too(api):
    http, tenant, documents = api

    response = await http.get(
        f"/documents/{documents[0].id}/relations",
        headers={"X-Tenant-Id": str(tenant.id)},
    )

    assert response.status_code == 200
    assert [r["target"]["document_id"] for r in response.json()] == [str(documents[1].id)]


async def test_another_tenant_gets_a_404(api, db_session):
    http, _, documents = api
    other = Tenant(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
    db_session.add(other)
    await db_session.flush()

    response = await http.get(
        f"/documents/{documents[0].id}/relations",
        headers={"X-Tenant-Id": str(other.id)},
    )

    assert response.status_code == 404


async def test_an_unknown_document_is_a_404(api):
    http, tenant, _ = api

    response = await http.get(
        f"/documents/{uuid.uuid4()}/relations", headers={"X-Tenant-Id": str(tenant.id)}
    )

    assert response.status_code == 404


async def test_a_document_with_no_relations_returns_an_empty_list(api, db_session):
    http, tenant, _ = api
    lonely = Document(
        tenant_id=tenant.id,
        filename="alone.pdf",
        mime_type="application/pdf",
        storage_key=f"{tenant.id}/alone",
        file_hash=uuid.uuid4().hex * 2,
        status=DocumentStatus.ready,
    )
    db_session.add(lonely)
    await db_session.flush()

    response = await http.get(
        f"/documents/{lonely.id}/relations", headers={"X-Tenant-Id": str(tenant.id)}
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_the_tenant_header_is_required(api):
    http, _, documents = api

    response = await http.get(f"/documents/{documents[0].id}/relations")

    assert response.status_code == 400
