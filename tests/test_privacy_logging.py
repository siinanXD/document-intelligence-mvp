"""Privacy-safe request logging: identifiers yes, bodies and secrets no."""

import uuid

import httpx
import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.api.dependencies import get_session
from app.api.request_logging import _audit_observers
from app.main import create_app
from app.models import Chunk, Document, DocumentStatus, Tenant
from app.services.indexing import index_document
from app.services.qa import GroundedAnswer
from app.services.vector_store import VectorStoreService
from tests.test_indexing import _FakeEmbeddings
from tests.test_qa import _FakeLLM

QUESTION = "SECRET_QUESTION_WHEN_IS_PAYMENT_DUE_FOR_INV_999"
PASSAGE = "SECRET_PASSAGE_payment is due on invoice INV-999."
API_KEY_HEADER = "Bearer sk-this-must-never-appear-in-logs"
ALLOWED_AUDIT_KEYS = {
    "request_id",
    "method",
    "path",
    "status",
    "duration_ms",
    "tenant_id",
    "document_id",
    "error_type",
}


@pytest.fixture
def audit_log():
    """Capture request-audit extras without depending on logging handlers."""
    records: list[dict] = []
    _audit_observers.append(records.append)
    try:
        yield records
    finally:
        _audit_observers.remove(records.append)


@pytest_asyncio.fixture
async def api(db_session, monkeypatch):
    client = AsyncQdrantClient(":memory:")
    store = VectorStoreService(client=client, collection="test_privacy_logs")
    embeddings = _FakeEmbeddings()
    llm = _FakeLLM(
        GroundedAnswer(
            answer="SECRET_MODEL_ANSWER_thirty_days",
            source_ids=[],
            has_sufficient_evidence=True,
        )
    )

    tenant = Tenant(slug=f"priv-{uuid.uuid4().hex[:8]}", name="Privacy")
    db_session.add(tenant)
    await db_session.flush()
    document = Document(
        tenant_id=tenant.id,
        filename="secret-contract.pdf",
        mime_type="application/pdf",
        storage_key=f"{tenant.id}/doc",
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
            text=PASSAGE,
            source_id=f"{document.id}:00000",
        )
    )
    await db_session.flush()
    await index_document(
        db_session, embeddings, store, tenant_id=tenant.id, document_id=document.id
    )

    monkeypatch.setattr("app.api.ask.get_embedding_provider", lambda: embeddings)
    monkeypatch.setattr("app.api.ask.get_llm_provider", lambda: llm)
    monkeypatch.setattr("app.api.ask.VectorStoreService", lambda: store)
    monkeypatch.setattr("app.api.search.get_embedding_provider", lambda: embeddings)
    monkeypatch.setattr("app.api.search.VectorStoreService", lambda: store)

    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as http:
        yield http, tenant, document

    await client.close()


def _blob(entries: list[dict]) -> str:
    return " ".join(f"{key}={value}" for extra in entries for key, value in extra.items())


async def test_request_logs_carry_identifiers_not_bodies(api, audit_log):
    http, tenant, document = api
    request_id = str(uuid.uuid4())

    response = await http.post(
        "/ask",
        headers={
            "X-Tenant-Id": str(tenant.id),
            "X-Request-Id": request_id,
            "Authorization": API_KEY_HEADER,
        },
        json={"question": QUESTION, "document_ids": [str(document.id)]},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id
    assert audit_log
    extra = audit_log[-1]
    assert set(extra) <= ALLOWED_AUDIT_KEYS
    assert extra["request_id"] == request_id
    assert extra["tenant_id"] == str(tenant.id)
    assert extra["status"] == 200
    assert extra["path"] == "/ask"
    assert "duration_ms" in extra

    blob = _blob(audit_log)
    assert QUESTION not in blob
    assert PASSAGE not in blob
    assert "SECRET_MODEL_ANSWER" not in blob
    assert "sk-this-must-never-appear-in-logs" not in blob
    assert "secret-contract.pdf" not in blob
    assert API_KEY_HEADER not in blob


def test_middleware_emits_an_audit_line_on_a_tiny_app(audit_log):
    """Prove the middleware logs when it is actually on the ASGI stack."""
    from fastapi.testclient import TestClient

    tiny = create_app()

    with TestClient(tiny) as client:
        response = client.get("/health")
        assert response.status_code == 200
        logged = client.get("/docs")
        assert logged.status_code == 200

    paths = [extra["path"] for extra in audit_log]
    assert "/health" not in paths
    assert "/docs" in paths
    assert all("request_id" in extra for extra in audit_log)


async def test_health_is_not_audit_logged(api, audit_log):
    http, _, _ = api

    assert (await http.get("/health")).status_code == 200
    assert audit_log == []


async def test_an_error_logs_the_type_not_the_body(api, audit_log):
    http, tenant, _ = api

    response = await http.post(
        "/ask",
        headers={"X-Tenant-Id": str(tenant.id)},
        json={"question": QUESTION, "limit": 0},
    )

    assert response.status_code == 422
    assert audit_log
    extra = audit_log[-1]
    assert extra["error_type"] == "http_422"
    assert extra["status"] == 422
    assert QUESTION not in _blob(audit_log)
