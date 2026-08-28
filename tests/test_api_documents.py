"""The upload endpoint end to end.

These drive the real routes against a real PostgreSQL and a temporary local
storage root: an upload must produce a document row, a stored object and a
queued ingestion job together, or none of them.
"""

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.dependencies import get_session, get_storage
from app.main import create_app
from app.models import Document, DocumentStatus, IngestionJob, JobStatus, Tenant
from app.providers.local_storage import LocalStorageBackend

PDF = b"%PDF-1.7\ncontract body"
OTHER_PDF = b"%PDF-1.7\na different contract"


@pytest_asyncio.fixture
async def api(db_session, tmp_path):
    """A client wired to the test transaction and a temporary storage root.

    The app's own session dependency is overridden so the request works inside
    the same rolled-back transaction as the test, rather than committing to the
    database and leaking state into the next test.

    httpx over an ASGI transport, not TestClient: TestClient drives the app on
    its own event loop, and the asyncpg connection behind db_session belongs to
    this test's loop. Sharing it across the two fails at runtime.
    """
    storage = LocalStorageBackend(tmp_path / "objects")
    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override
    application.dependency_overrides[get_storage] = lambda: storage

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client, storage


@pytest_asyncio.fixture
async def api_tenant(db_session) -> Tenant:
    tenant = Tenant(slug=f"api-{uuid.uuid4().hex[:8]}", name="Api")
    db_session.add(tenant)
    await db_session.flush()
    return tenant


async def _upload(client, tenant, content=PDF, filename="contract.pdf", mime="application/pdf"):
    return await client.post(
        "/documents",
        headers={"X-Tenant-Id": str(tenant.id)},
        files={"file": (filename, content, mime)},
    )


async def test_an_upload_creates_document_object_and_job(api, api_tenant, db_session):
    client, storage = api

    response = await _upload(client, api_tenant)

    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    assert body["document"]["status"] == "queued"
    assert body["document"]["mime_type"] == "application/pdf"

    document_id = uuid.UUID(body["document"]["id"])
    document = (
        (await db_session.execute(select(Document).where(Document.id == document_id)))
        .scalars()
        .one()
    )
    assert document.tenant_id == api_tenant.id
    assert document.status is DocumentStatus.queued

    # The object is where the row says it is, byte for byte.
    assert await storage.get(document.storage_key) == PDF

    job = (
        (
            await db_session.execute(
                select(IngestionJob).where(IngestionJob.document_id == document_id)
            )
        )
        .scalars()
        .one()
    )
    assert job.status is JobStatus.queued
    assert job.tenant_id == api_tenant.id


async def test_the_storage_key_is_built_from_identifiers(api, api_tenant, db_session):
    client, _ = api

    body = (await _upload(client, api_tenant, filename="../../escape.pdf")).json()
    document_id = body["document"]["id"]
    document = (
        (await db_session.execute(select(Document).where(Document.id == uuid.UUID(document_id))))
        .scalars()
        .one()
    )

    assert document.storage_key == f"{api_tenant.id}/{document_id}/escape.pdf"
    assert ".." not in document.storage_key


async def test_re_uploading_identical_bytes_is_reported_as_a_duplicate(api, api_tenant, db_session):
    client, _ = api

    first = await _upload(client, api_tenant)
    second = await _upload(client, api_tenant)

    assert first.status_code == 201
    # Nothing was created, so this is not a 201.
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]

    documents = (
        (await db_session.execute(select(Document).where(Document.tenant_id == api_tenant.id)))
        .scalars()
        .all()
    )
    assert len(documents) == 1

    # And no second job: a duplicate must not be reprocessed.
    jobs = (
        (
            await db_session.execute(
                select(IngestionJob).where(IngestionJob.tenant_id == api_tenant.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1


async def test_a_duplicate_is_only_a_duplicate_within_one_tenant(api, api_tenant, db_session):
    """Tenant A's upload must never be matched against tenant B's."""
    client, _ = api
    other = Tenant(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
    db_session.add(other)
    await db_session.flush()

    first = await _upload(client, api_tenant)
    second = await _upload(client, other)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["duplicate"] is False
    assert second.json()["document"]["id"] != first.json()["document"]["id"]


async def test_different_bytes_are_not_a_duplicate(api, api_tenant):
    client, _ = api

    first = await _upload(client, api_tenant)
    second = await _upload(client, api_tenant, content=OTHER_PDF)

    assert second.status_code == 201
    assert second.json()["document"]["id"] != first.json()["document"]["id"]


@pytest.mark.parametrize(
    ("filename", "content", "mime", "expected_status"),
    [
        ("archive.zip", b"PK\x03\x04", "application/zip", 415),
        ("contract.pdf", b"<html>not a pdf</html>", "application/pdf", 415),
        ("contract.pdf", b"%PDF-1.7", "text/html", 415),
        ("empty.pdf", b"", "application/pdf", 400),
    ],
)
async def test_bad_uploads_are_refused_with_a_useful_status(
    api, api_tenant, filename, content, mime, expected_status
):
    client, _ = api

    response = await _upload(client, api_tenant, content=content, filename=filename, mime=mime)

    assert response.status_code == expected_status
    assert response.json()["detail"]


async def test_an_upload_over_the_limit_is_refused(api, api_tenant, monkeypatch):
    from app.core.settings import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "16")
    assert Settings().max_upload_bytes == 16
    client, _ = api

    response = await _upload(client, api_tenant, content=b"%PDF-" + b"x" * 100)

    assert response.status_code == 413


async def test_a_refused_upload_stores_nothing(api, api_tenant, db_session):
    client, storage = api

    await _upload(client, api_tenant, filename="archive.zip", mime="application/zip")

    documents = (
        (await db_session.execute(select(Document).where(Document.tenant_id == api_tenant.id)))
        .scalars()
        .all()
    )
    assert documents == []


async def test_listing_returns_only_this_tenants_documents(api, api_tenant, db_session):
    client, _ = api
    other = Tenant(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
    db_session.add(other)
    await db_session.flush()

    await _upload(client, api_tenant)
    await _upload(client, other, content=OTHER_PDF)

    mine = (await client.get("/documents", headers={"X-Tenant-Id": str(api_tenant.id)})).json()

    assert len(mine) == 1
    assert mine[0]["filename"] == "contract.pdf"


async def test_another_tenant_gets_a_404_not_a_403(api, api_tenant, db_session):
    """A 403 would confirm the id exists. Absence is the safer answer."""
    client, _ = api
    other = Tenant(slug=f"other-{uuid.uuid4().hex[:8]}", name="Other")
    db_session.add(other)
    await db_session.flush()

    document_id = (await _upload(client, api_tenant)).json()["document"]["id"]

    response = await client.get(f"/documents/{document_id}", headers={"X-Tenant-Id": str(other.id)})

    assert response.status_code == 404


async def test_fetching_one_document_works_for_its_owner(api, api_tenant):
    client, _ = api
    document_id = (await _upload(client, api_tenant)).json()["document"]["id"]

    response = await client.get(
        f"/documents/{document_id}", headers={"X-Tenant-Id": str(api_tenant.id)}
    )

    assert response.status_code == 200
    assert response.json()["id"] == document_id


async def test_a_missing_document_is_a_404(api, api_tenant):
    client, _ = api

    response = await client.get(
        f"/documents/{uuid.uuid4()}", headers={"X-Tenant-Id": str(api_tenant.id)}
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 400),
        ({"X-Tenant-Id": "not-a-uuid"}, 400),
        ({"X-Tenant-Id": str(uuid.uuid4())}, 404),
    ],
)
async def test_the_tenant_header_is_required_and_must_resolve(api, headers, expected_status):
    client, _ = api

    assert (await client.get("/documents", headers=headers)).status_code == expected_status
