"""The PostgreSQL-backed ingestion job queue.

The concurrency tests use two independent connections rather than two sessions
on one connection: `FOR UPDATE SKIP LOCKED` only means anything across real
transactions, and a single-connection test would pass whether or not the lock
were there.
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import JobStatus
from app.services import jobs


async def test_enqueue_creates_a_queued_job(db_session, tenant, make_document):
    document = await make_document(tenant)

    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    assert job.status is JobStatus.queued
    assert job.attempts == 0
    assert job.tenant_id == tenant.id


async def test_enqueueing_twice_returns_the_job_already_in_flight(
    db_session, tenant, make_document
):
    document = await make_document(tenant)

    first = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    second = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    assert first.id == second.id


async def test_the_database_refuses_a_second_live_job_for_one_document(
    db_session, tenant, make_document
):
    """The guarantee is the partial unique index, not the service's check."""
    from sqlalchemy.exc import IntegrityError

    from app.models import IngestionJob

    document = await make_document(tenant)
    await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    db_session.add(IngestionJob(tenant_id=tenant.id, document_id=document.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_claiming_marks_the_job_processing_and_counts_the_attempt(
    db_session, tenant, make_document
):
    document = await make_document(tenant)
    await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    claimed = await jobs.claim(db_session, worker_id="worker-1", limit=5)

    assert len(claimed) == 1
    assert claimed[0].status is JobStatus.processing
    assert claimed[0].claimed_by == "worker-1"
    assert claimed[0].attempts == 1


async def test_a_claimed_job_is_not_handed_out_again(db_session, tenant, make_document):
    document = await make_document(tenant)
    await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    await jobs.claim(db_session, worker_id="worker-1")

    assert await jobs.claim(db_session, worker_id="worker-2") == []


async def test_a_job_scheduled_for_later_is_not_claimable_yet(db_session, tenant, make_document):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    await db_session.execute(
        text("UPDATE ingestion_jobs SET available_at = now() + interval '1 hour' WHERE id = :id"),
        {"id": job.id},
    )

    assert await jobs.claim(db_session, worker_id="worker-1") == []


async def test_claim_rejects_a_meaningless_limit(db_session):
    with pytest.raises(ValueError, match="at least 1"):
        await jobs.claim(db_session, worker_id="worker-1", limit=0)


async def test_finish_clears_the_claim(db_session, tenant, make_document):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    await jobs.claim(db_session, worker_id="worker-1")

    finished = await jobs.finish(
        db_session, tenant_id=tenant.id, job_id=job.id, worker_id="worker-1"
    )

    assert finished.status is JobStatus.finished
    assert finished.claimed_by is None


async def test_a_failure_with_attempts_left_is_requeued_for_later(
    db_session, tenant, make_document
):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    await jobs.claim(db_session, worker_id="worker-1")

    failed = await jobs.fail(
        db_session,
        tenant_id=tenant.id,
        job_id=job.id,
        worker_id="worker-1",
        error="parser returned no pages",
        retry_delay=timedelta(minutes=5),
    )

    assert failed.status is JobStatus.queued
    assert failed.last_error == "parser returned no pages"
    # The backoff must actually hold the job back.
    assert await jobs.claim(db_session, worker_id="worker-2") == []


async def test_a_failure_with_no_attempts_left_stays_failed(db_session, tenant, make_document):
    document = await make_document(tenant)
    job = await jobs.enqueue(
        db_session, tenant_id=tenant.id, document_id=document.id, max_attempts=1
    )
    await jobs.claim(db_session, worker_id="worker-1")

    failed = await jobs.fail(
        db_session,
        tenant_id=tenant.id,
        job_id=job.id,
        worker_id="worker-1",
        error="unsupported format",
    )

    assert failed.status is JobStatus.failed
    assert await jobs.claim(db_session, worker_id="worker-2") == []


async def test_a_long_error_is_truncated_rather_than_rejected(db_session, tenant, make_document):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    await jobs.claim(db_session, worker_id="worker-1")

    failed = await jobs.fail(
        db_session,
        tenant_id=tenant.id,
        job_id=job.id,
        worker_id="worker-1",
        error="x" * 5000,
    )

    assert len(failed.last_error) == 2000


async def test_two_workers_racing_never_receive_the_same_job(db_engine, db_session):
    """The core guarantee, proved across two real transactions.

    Committed rows are needed here, so this test writes its own tenant and
    documents on a separate connection and cleans them up afterwards.
    """
    import uuid

    from app.models import Document, DocumentStatus, IngestionJob, Tenant

    sessionmaker = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    slug = f"race-{uuid.uuid4().hex[:8]}"

    async with sessionmaker() as setup:
        tenant = Tenant(slug=slug, name="Race")
        setup.add(tenant)
        await setup.flush()
        for index in range(4):
            document = Document(
                tenant_id=tenant.id,
                filename=f"doc-{index}.pdf",
                mime_type="application/pdf",
                storage_key=f"{tenant.id}/{index}",
                file_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                status=DocumentStatus.queued,
            )
            setup.add(document)
            await setup.flush()
            setup.add(IngestionJob(tenant_id=tenant.id, document_id=document.id))
        await setup.commit()
        tenant_id = tenant.id

    async def claim_as(worker_id: str) -> set:
        async with sessionmaker() as session, session.begin():
            claimed = await jobs.claim(session, worker_id=worker_id, limit=4)
            # Hold the transaction open long enough for the workers to overlap.
            await asyncio.sleep(0.2)
            return {job.id for job in claimed}

    try:
        first, second = await asyncio.gather(claim_as("worker-1"), claim_as("worker-2"))

        assert first & second == set(), "the same job was handed to two workers"
        assert len(first) + len(second) == 4, "a job was lost between the two workers"
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})
            await cleanup.commit()


async def test_claiming_keeps_the_session_in_step_with_the_database(
    db_session, tenant, make_document
):
    """A bulk UPDATE must not leave stale objects behind.

    `claim` updates rows in one statement. If the session's already-loaded
    copies were not synchronised, `fail` would read the pre-claim `attempts`
    and re-queue a job whose attempts are spent - retrying it forever.
    """
    document = await make_document(tenant)
    job = await jobs.enqueue(
        db_session, tenant_id=tenant.id, document_id=document.id, max_attempts=1
    )
    assert job.attempts == 0

    await jobs.claim(db_session, worker_id="worker-1")

    # Same object, read through the session that issued the bulk update.
    assert job.attempts == 1
    assert job.status is JobStatus.processing


async def test_a_queued_job_cannot_be_finished(db_session, tenant, make_document):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    assert (
        await jobs.finish(
            db_session, tenant_id=tenant.id, job_id=job.id, worker_id="worker-1"
        )
        is None
    )
    assert job.status is JobStatus.queued


async def test_a_different_worker_cannot_finish_or_fail_a_claim(
    db_session, tenant, make_document
):
    document = await make_document(tenant)
    job = await jobs.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    await jobs.claim(db_session, worker_id="worker-1")

    assert (
        await jobs.finish(
            db_session, tenant_id=tenant.id, job_id=job.id, worker_id="worker-2"
        )
        is None
    )
    assert (
        await jobs.fail(
            db_session,
            tenant_id=tenant.id,
            job_id=job.id,
            worker_id="worker-2",
            error="not this worker's claim",
        )
        is None
    )
    assert job.status is JobStatus.processing
    assert job.claimed_by == "worker-1"
