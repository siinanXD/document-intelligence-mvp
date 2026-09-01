"""Ingestion job queue backed by PostgreSQL.

Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED` inside the same statement
that marks the row as taken, so two workers racing on the same queue can never
be handed the same job: the second one skips the locked row instead of waiting
for it. No Redis, no broker.
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IngestionJob, JobStatus

logger = logging.getLogger(__name__)

DEFAULT_RETRY_DELAY = timedelta(minutes=1)


async def enqueue(
    session: AsyncSession, *, tenant_id, document_id, max_attempts: int = 3
) -> IngestionJob:
    """Queue ingestion for a document, or return the job already in flight.

    The partial unique index on live jobs makes this safe to call twice; this
    checks first so the caller gets the existing job rather than an error.
    """
    existing = await get_live_job(session, tenant_id=tenant_id, document_id=document_id)
    if existing is not None:
        return existing

    job = IngestionJob(
        tenant_id=tenant_id,
        document_id=document_id,
        status=JobStatus.queued,
        max_attempts=max_attempts,
    )
    session.add(job)
    await session.flush()
    return job


async def get_live_job(session: AsyncSession, *, tenant_id, document_id) -> IngestionJob | None:
    """Return the queued or processing job for a document within one tenant."""
    result = await session.execute(
        select(IngestionJob).where(
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.document_id == document_id,
            IngestionJob.status.in_((JobStatus.queued, JobStatus.processing)),
        )
    )
    return result.scalars().first()


async def get_latest_job(session: AsyncSession, *, tenant_id, document_id) -> IngestionJob | None:
    """Return the most recently updated job for a document within one tenant."""
    result = await session.execute(
        select(IngestionJob)
        .where(IngestionJob.tenant_id == tenant_id, IngestionJob.document_id == document_id)
        .order_by(IngestionJob.updated_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_job(session: AsyncSession, *, tenant_id, job_id) -> IngestionJob | None:
    """Fetch one job. Scoped to a tenant: another tenant's job reads as absent."""
    result = await session.execute(
        select(IngestionJob).where(IngestionJob.id == job_id, IngestionJob.tenant_id == tenant_id)
    )
    return result.scalars().first()


async def claim(session: AsyncSession, *, worker_id: str, limit: int = 1) -> list[IngestionJob]:
    """Claim up to `limit` due jobs for this worker.

    Deliberately not tenant-scoped: a worker serves every tenant, and the rows
    it claims carry their own `tenant_id` for everything that follows. This is
    the one place that reads across tenants, and it hands back whole rows
    rather than answering a question about another tenant's data.

    `attempts` is incremented at claim time, not at completion, so a worker
    that dies mid-job still consumes an attempt and cannot retry forever.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")

    due = (
        select(IngestionJob.id)
        .where(
            IngestionJob.status == JobStatus.queued,
            IngestionJob.available_at <= func.now(),
        )
        .order_by(IngestionJob.available_at, IngestionJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )

    result = await session.execute(
        update(IngestionJob)
        .where(IngestionJob.id.in_(due))
        .values(
            status=JobStatus.processing,
            claimed_at=func.now(),
            claimed_by=worker_id,
            attempts=IngestionJob.attempts + 1,
            updated_at=func.now(),
        )
        .returning(IngestionJob)
        # "fetch" keeps objects already loaded in this session in step with
        # the bulk update. With synchronize_session=False a caller that claims
        # and then fails a job in one session reads a stale `attempts` and
        # re-queues a job whose attempts are spent - retrying it forever.
        .execution_options(synchronize_session="fetch")
    )
    return list(result.scalars().all())


async def finish(
    session: AsyncSession, *, tenant_id, job_id, worker_id: str
) -> IngestionJob | None:
    """Finish only a processing job claimed by this worker.

    The predicate and transition are one statement, so a stale or foreign
    worker cannot win a check-then-update race.
    """
    result = await session.execute(
        update(IngestionJob)
        .where(
            IngestionJob.id == job_id,
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.status == JobStatus.processing,
            IngestionJob.claimed_by == worker_id,
        )
        .values(
            status=JobStatus.finished,
            last_error=None,
            claimed_at=None,
            claimed_by=None,
            updated_at=func.now(),
        )
        .returning(IngestionJob)
        .execution_options(synchronize_session="fetch")
    )
    return result.scalars().first()


async def fail(
    session: AsyncSession,
    *,
    tenant_id,
    job_id,
    worker_id: str,
    error: str,
    retry_delay: timedelta = DEFAULT_RETRY_DELAY,
) -> IngestionJob | None:
    """Fail only a processing job claimed by this worker.

    The row lock makes the ownership check and retry decision one atomic state
    transition. A stale, foreign or duplicate completion returns None.
    """
    result = await session.execute(
        select(IngestionJob)
        .where(
            IngestionJob.id == job_id,
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.status == JobStatus.processing,
            IngestionJob.claimed_by == worker_id,
        )
        .with_for_update()
    )
    job = result.scalars().first()
    if job is None:
        return None

    # This is a diagnostic message. Never pass document content, a prompt or a
    # completion: this column is read by anyone with database access.
    job.last_error = error[:2000]
    job.claimed_at = None
    job.claimed_by = None

    if job.attempts >= job.max_attempts:
        job.status = JobStatus.failed
        logger.warning(
            "ingestion job exhausted its attempts",
            extra={"job_id": str(job.id), "tenant_id": str(job.tenant_id)},
        )
    else:
        job.status = JobStatus.queued
        job.available_at = func.now() + retry_delay

    await session.flush()
    return job
