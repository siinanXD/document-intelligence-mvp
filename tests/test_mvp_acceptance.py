"""SIN-70: one HTTP scenario covering the MVP acceptance slice.

Uploads a PDF contract, a versioned PDF, a related DOCX, an unrelated text
file and a content-duplicate PDF. Processing uses a filename-aware fake parser
and deterministic fake embeddings/LLM so CI never makes a paid call. Live
Railway smoke is item 14 and stays a documented owner command (SIN-97).
"""

import re
import uuid

import httpx
import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from app.api.dependencies import get_session, get_storage
from app.main import create_app
from app.models import Document, Tenant
from app.providers.generation import generation_from_prompt
from app.providers.local_storage import LocalStorageBackend
from app.providers.parsing import ParsedChunk, ParsedDocument
from app.services import jobs as jobs_service
from app.services.processing import process_job
from app.services.profiling import ExtractedProfile
from app.services.qa import GroundedAnswer
from app.services.vector_store import DocumentVectorStore, VectorStoreService
from tests.test_indexing import _FakeEmbeddings

WORKER = "mvp-acceptance"
PDF = b"%PDF-1.7\n"
DOCX = b"PK\x03\x04" + b"\x00" * 40
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

CONTRACT_TEXT = (
    "Service Agreement CASE-2024-17 effective 2024-01-01. "
    "Acme agrees. Payment is due within thirty days of invoice."
)
VERSION_TEXT = (
    "Service Agreement CASE-2024-17 effective 2024-01-01. "
    "Acme agrees. Payment is due within forty-five days of invoice."
)
ADDENDUM_TEXT = (
    "Addendum CASE-2024-17 effective 2024-01-01. "
    "Acme agrees. Either party may terminate with three months notice."
)
WEATHER_TEXT = "Weekend weather outlook for Umbrella Corp. Sunny skies expected."

TEXTS = {
    "contract.pdf": CONTRACT_TEXT,
    "contract-v2.pdf": VERSION_TEXT,
    "addendum.docx": ADDENDUM_TEXT,
    "weather.txt": WEATHER_TEXT,
    "contract-reprint.pdf": CONTRACT_TEXT,
}


class _ScenarioParser:
    """Returns fixture text keyed by filename. No Docling, no document quotes."""

    name = "fake"

    def supports(self, mime_type: str) -> bool:
        return True

    async def parse(self, *, filename, mime_type, content):
        text = TEXTS[filename]
        section = "Payment" if "Payment" in text else "Body"
        chunks = [
            ParsedChunk(
                ordinal=0,
                text=text.split(". ", 1)[0],
                page_number=1,
                section_title="Title",
            ),
            ParsedChunk(ordinal=1, text=text, page_number=1, section_title=section),
        ]
        return ParsedDocument(text=text, chunks=chunks, serialized=b'{"schema":"fake"}')


class _ScenarioLLM:
    """Profiles from excerpt keywords; answers from supplied source ids only."""

    provider = "fake"
    model = "fake-llm"

    async def complete(self, prompt, user: str) -> str:
        raise AssertionError("MVP paths must use structured generation")

    async def complete_structured(self, prompt, user: str, schema):
        if schema is ExtractedProfile:
            content = ExtractedProfile(
                title="Service Agreement" if "Service Agreement" in user else None,
                organizations=[name for name in ("Acme", "Umbrella") if name in user],
                dates=["2024-01-01"] if "2024-01-01" in user else [],
                identifiers=["CASE-2024-17"] if "CASE-2024-17" in user else [],
            )
        else:
            content = _answer_from_sources(user)
        return generation_from_prompt(content, prompt, provider=self.provider, model=self.model)


def _answer_from_sources(user: str) -> GroundedAnswer:
    blocks = re.findall(
        r"\[source_id: ([^\]]+)\][^\n]*\n(.*?)(?=\n\[source_id: |\n\nQuestion: |\Z)",
        user,
        re.S,
    )
    payment = [(source_id, text) for source_id, text in blocks if "payment is due" in text.lower()]
    if len(payment) >= 2 and "thirty" in user.lower() and "forty-five" in user.lower():
        return GroundedAnswer(
            answer="The documents disagree on the payment period.",
            source_ids=[source_id for source_id, _ in payment],
            has_sufficient_evidence=True,
            conflicting=True,
        )
    if payment:
        return GroundedAnswer(
            answer="Payment is due as stated in the cited passage.",
            source_ids=[payment[0][0]],
            has_sufficient_evidence=True,
        )
    return GroundedAnswer(
        answer="The documents do not answer this question.",
        source_ids=[],
        has_sufficient_evidence=False,
    )


def _headers(tenant: Tenant) -> dict[str, str]:
    return {"X-Tenant-Id": str(tenant.id)}


async def _upload(http, tenant, *, filename: str, content: bytes, mime: str):
    return await http.post(
        "/documents",
        headers=_headers(tenant),
        files={"file": (filename, content, mime)},
    )


async def _process_queue(session, storage, parser, embeddings, chunks, documents, llm):
    claimed = await jobs_service.claim(session, worker_id=WORKER, limit=20)
    for job in claimed:
        await process_job(
            session,
            storage,
            parser,
            job=job,
            embeddings=embeddings,
            vector_store=chunks,
            llm=llm,
            document_vectors=documents,
        )
        finished = await jobs_service.finish(
            session, tenant_id=job.tenant_id, job_id=job.id, worker_id=WORKER
        )
        assert finished is not None


@pytest_asyncio.fixture
async def mvp(db_session, tmp_path, monkeypatch):
    client = AsyncQdrantClient(":memory:")
    chunks = VectorStoreService(client=client, collection="mvp_chunks")
    documents = DocumentVectorStore(client=client, collection="mvp_documents")
    embeddings = _FakeEmbeddings()
    llm = _ScenarioLLM()
    parser = _ScenarioParser()
    storage = LocalStorageBackend(tmp_path / "objects")

    tenant = Tenant(slug=f"mvp-{uuid.uuid4().hex[:8]}", name="MVP")
    outsider = Tenant(slug=f"out-{uuid.uuid4().hex[:8]}", name="Outsider")
    db_session.add_all([tenant, outsider])
    await db_session.flush()

    for module in ("app.api.search", "app.api.ask", "app.api.documents"):
        monkeypatch.setattr(f"{module}.get_embedding_provider", lambda: embeddings)
        monkeypatch.setattr(f"{module}.VectorStoreService", lambda: chunks)
    monkeypatch.setattr("app.api.ask.get_llm_provider", lambda: llm)
    monkeypatch.setattr("app.api.documents.DocumentVectorStore", lambda: documents)

    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override
    application.dependency_overrides[get_storage] = lambda: storage

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as http:
        ids = {}
        uploads = (
            ("contract.pdf", PDF + b"contract-v1", "application/pdf"),
            ("contract-v2.pdf", PDF + b"contract-v2", "application/pdf"),
            ("addendum.docx", DOCX + b"addendum", DOCX_MIME),
            ("weather.txt", b"Weekend weather outlook for Umbrella Corp.", "text/plain"),
            ("contract-reprint.pdf", PDF + b"reprint-bytes", "application/pdf"),
        )
        for filename, content, mime in uploads:
            response = await _upload(http, tenant, filename=filename, content=content, mime=mime)
            assert response.status_code == 201, response.text
            ids[filename] = response.json()["document"]["id"]
            await _process_queue(db_session, storage, parser, embeddings, chunks, documents, llm)

        yield {
            "http": http,
            "tenant": tenant,
            "outsider": outsider,
            "ids": ids,
            "storage": storage,
            "chunks": chunks,
            "session": db_session,
        }

    await client.close()


async def test_uploaded_files_process_to_ready(mvp):
    http, tenant, ids = mvp["http"], mvp["tenant"], mvp["ids"]

    for filename, document_id in ids.items():
        document = await http.get(f"/documents/{document_id}", headers=_headers(tenant))
        assert document.status_code == 200
        assert document.json()["status"] == "ready"
        pipeline = await http.get(f"/documents/{document_id}/pipeline", headers=_headers(tenant))
        assert pipeline.status_code == 200
        body = pipeline.json()
        assert body["current_stage"] == "ready"
        assert all(stage["complete"] for stage in body["stages"]), filename


async def test_exact_duplicate_upload_is_detected(mvp):
    http, tenant, ids = mvp["http"], mvp["tenant"], mvp["ids"]

    response = await _upload(
        http, tenant, filename="contract.pdf", content=PDF + b"contract-v1", mime="application/pdf"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["duplicate"] is True
    assert body["document"]["id"] == ids["contract.pdf"]


async def test_normalized_content_duplicate_is_linked(mvp):
    http, tenant, ids = mvp["http"], mvp["tenant"], mvp["ids"]

    response = await http.get(
        f"/documents/{ids['contract-reprint.pdf']}/relations", headers=_headers(tenant)
    )
    assert response.status_code == 200
    types = {row["target"]["document_id"]: row["relation_type"] for row in response.json()}
    assert types[ids["contract.pdf"]] == "content_duplicate"


async def test_versioned_pdf_is_linked_and_unrelated_is_not(mvp):
    http, tenant, ids = mvp["http"], mvp["tenant"], mvp["ids"]

    versioned = await http.get(
        f"/documents/{ids['contract-v2.pdf']}/relations", headers=_headers(tenant)
    )
    assert versioned.status_code == 200
    by_target = {row["target"]["document_id"]: row for row in versioned.json()}
    assert by_target[ids["contract.pdf"]]["relation_type"] == "possible_version"
    assert ids["weather.txt"] not in by_target

    addendum = await http.get(
        f"/documents/{ids['addendum.docx']}/relations", headers=_headers(tenant)
    )
    addendum_types = {row["target"]["document_id"]: row["relation_type"] for row in addendum.json()}
    assert addendum_types[ids["contract.pdf"]] == "same_case"

    weather = await http.get(f"/documents/{ids['weather.txt']}/relations", headers=_headers(tenant))
    assert weather.status_code == 200
    assert weather.json() == []


async def test_semantic_search_within_one_document_and_across_tenant(mvp):
    http, tenant, ids = mvp["http"], mvp["tenant"], mvp["ids"]

    narrowed = await http.post(
        "/search",
        headers=_headers(tenant),
        json={"query": "payment", "document_ids": [ids["contract.pdf"]]},
    )
    assert narrowed.status_code == 200
    narrowed_docs = {row["document_id"] for row in narrowed.json()["results"]}
    assert narrowed_docs == {ids["contract.pdf"]}
    assert "thirty days" in narrowed.json()["results"][0]["text"]

    tenant_wide = await http.post(
        "/search", headers=_headers(tenant), json={"query": "payment", "limit": 10}
    )
    results = tenant_wide.json()["results"]
    wide_docs = {row["document_id"] for row in results}
    assert ids["contract.pdf"] in wide_docs
    assert ids["contract-v2.pdf"] in wide_docs
    best: dict[str, float] = {}
    for row in results:
        best[row["document_id"]] = max(best.get(row["document_id"], -1.0), row["score"])
    assert best[ids["contract.pdf"]] > best.get(ids["weather.txt"], 0.0)
    assert "payment" in results[0]["text"].lower()


async def test_ask_returns_resolvable_citations_and_surfaces_conflict(mvp):
    http, tenant, ids = mvp["http"], mvp["tenant"], mvp["ids"]

    response = await http.post(
        "/ask", headers=_headers(tenant), json={"question": "When is payment due?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["has_sufficient_evidence"] is True
    assert body["conflicting"] is True
    assert len(body["sources"]) >= 2
    cited_docs = {source["document_id"] for source in body["sources"]}
    assert ids["contract.pdf"] in cited_docs
    assert ids["contract-v2.pdf"] in cited_docs

    source = body["sources"][0]
    resolved = await http.get(
        f"/documents/{source['document_id']}/sources/{source['source_id']}",
        headers=_headers(tenant),
    )
    assert resolved.status_code == 200
    passage = resolved.json()
    assert passage["page_number"] == 1
    assert passage["section_title"]
    assert "Payment is due" in passage["text"]
    assert passage["source_id"] == source["source_id"]


async def test_deletion_removes_storage_rows_and_search_hits(mvp):
    http, tenant, ids, storage = mvp["http"], mvp["tenant"], mvp["ids"], mvp["storage"]
    weather_id = uuid.UUID(ids["weather.txt"])
    document = await mvp["session"].get(Document, weather_id)
    assert document is not None
    keys = [document.storage_key, document.normalized_key]

    listed = await http.get(f"/documents/{weather_id}", headers=_headers(tenant))
    assert listed.status_code == 200

    deleted = await http.delete(f"/documents/{weather_id}", headers=_headers(tenant))
    assert deleted.status_code == 204
    assert (await http.get(f"/documents/{weather_id}", headers=_headers(tenant))).status_code == 404

    search = await http.post(
        "/search",
        headers=_headers(tenant),
        json={"query": "weather", "document_ids": [str(weather_id)]},
    )
    assert search.status_code == 200
    assert search.json()["results"] == []

    for key in keys:
        if key:
            assert await storage.exists(key) is False


async def test_reindex_recreates_vectors_without_reupload(mvp):
    http, tenant, ids, chunks = mvp["http"], mvp["tenant"], mvp["ids"], mvp["chunks"]
    document_id = uuid.UUID(ids["contract.pdf"])

    await chunks.delete_document(tenant_id=mvp["tenant"].id, document_id=document_id)
    missing = await http.post(
        "/search",
        headers=_headers(tenant),
        json={"query": "payment", "document_ids": [str(document_id)]},
    )
    assert missing.json()["results"] == []

    response = await http.post(f"/documents/{document_id}/reindex", headers=_headers(tenant))
    assert response.status_code == 200
    assert response.json()["indexed"] >= 1

    restored = await http.post(
        "/search",
        headers=_headers(tenant),
        json={"query": "payment", "document_ids": [str(document_id)]},
    )
    assert restored.json()["results"]
    assert restored.json()["results"][0]["document_id"] == str(document_id)


async def test_another_tenant_cannot_see_the_acceptance_fixture(mvp):
    http, outsider, ids = mvp["http"], mvp["outsider"], mvp["ids"]
    document_id = ids["contract.pdf"]
    headers = _headers(outsider)

    assert (await http.get(f"/documents/{document_id}", headers=headers)).status_code == 404
    search = await http.post("/search", headers=headers, json={"query": "payment"})
    assert search.status_code == 200
    assert search.json()["results"] == []
    asked = await http.post("/ask", headers=headers, json={"question": "When is payment due?"})
    assert asked.status_code == 200
    assert asked.json()["has_sufficient_evidence"] is False
    assert asked.json()["sources"] == []
    assert (
        await http.get(f"/documents/{document_id}/relations", headers=headers)
    ).status_code == 404
    assert (await http.delete(f"/documents/{document_id}", headers=headers)).status_code == 404
