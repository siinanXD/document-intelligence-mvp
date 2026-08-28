"""Cross-tenant negative tests.

CLAUDE.md requires every feature that reads data to ship with a test proving
another tenant cannot see it. These are those tests for the data model: each
one asks for a real row using the wrong tenant_id and asserts the answer is
"nothing", not the row.
"""

from app.models import Chunk
from app.services import documents, jobs


async def test_a_document_is_invisible_to_another_tenant(
    db_session, tenant, other_tenant, make_document
):
    document = await make_document(tenant)

    assert (
        await documents.get_document(db_session, tenant_id=other_tenant.id, document_id=document.id)
        is None
    )


async def test_listing_returns_only_the_asking_tenants_documents(
    db_session, tenant, other_tenant, make_document
):
    mine = await make_document(tenant)
    await make_document(other_tenant)

    visible = await documents.list_documents(db_session, tenant_id=tenant.id)

    assert [document.id for document in visible] == [mine.id]


async def test_a_soft_deleted_document_disappears_from_its_own_tenant(
    db_session, tenant, make_document
):
    from datetime import UTC, datetime

    document = await make_document(tenant)
    document.deleted_at = datetime.now(UTC)
    await db_session.flush()

    assert (
        await documents.get_document(db_session, tenant_id=tenant.id, document_id=document.id)
        is None
    )
    assert await documents.list_documents(db_session, tenant_id=tenant.id) == []


async def test_duplicate_detection_does_not_reach_across_tenants(
    db_session, tenant, other_tenant, make_document
):
    """Two tenants holding identical bytes must not be told they are duplicates."""
    shared_hash = "b" * 64
    await make_document(tenant, file_hash=shared_hash)
    await make_document(other_tenant, file_hash=shared_hash)

    found = await documents.find_by_file_hash(
        db_session, tenant_id=tenant.id, file_hash=shared_hash
    )

    assert found is not None
    assert found.tenant_id == tenant.id


async def test_content_hash_matching_does_not_reach_across_tenants(
    db_session, tenant, other_tenant, make_document
):
    shared_content = "c" * 64
    await make_document(tenant, content_hash=shared_content)
    await make_document(other_tenant, content_hash=shared_content)

    found = await documents.find_by_content_hash(
        db_session, tenant_id=tenant.id, content_hash=shared_content
    )

    assert len(found) == 1
    assert found[0].tenant_id == tenant.id


async def test_chunks_are_invisible_to_another_tenant(
    db_session, tenant, other_tenant, make_document
):
    document = await make_document(tenant)
    db_session.add(
        Chunk(
            tenant_id=tenant.id,
            document_id=document.id,
            ordinal=0,
            text="confidential clause",
            source_id=f"{document.id}:0",
        )
    )
    await db_session.flush()

    assert (
        await documents.list_chunks(db_session, tenant_id=other_tenant.id, document_id=document.id)
        == []
    )


async def test_a_job_is_invisible_to_another_tenant(
    db_session, tenant, other_tenant, make_document
):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    assert await jobs.get_job(db_session, tenant_id=other_tenant.id, job_id=job.id) is None


async def test_another_tenant_cannot_finish_or_fail_a_job(
    db_session, tenant, other_tenant, make_document
):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    assert await jobs.finish(db_session, tenant_id=other_tenant.id, job_id=job.id) is None
    assert (
        await jobs.fail(db_session, tenant_id=other_tenant.id, job_id=job.id, error="not yours")
        is None
    )

    # The job is untouched.
    mine = await jobs.get_job(db_session, tenant_id=tenant.id, job_id=job.id)
    assert mine.status.value == "queued"
    assert mine.last_error is None


async def test_deleting_a_tenant_removes_only_its_own_rows(
    db_session, tenant, other_tenant, make_document
):
    mine = await make_document(tenant)
    theirs = await make_document(other_tenant)
    await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=mine.id)

    await db_session.delete(tenant)
    await db_session.flush()

    assert (
        await documents.get_document(db_session, tenant_id=other_tenant.id, document_id=theirs.id)
        is not None
    )
