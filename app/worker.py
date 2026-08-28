"""The ingestion worker.

Claims jobs from PostgreSQL, processes them, and records the outcome. Runs in
the same Python project as the API - one codebase, two entry points - and
shares its settings, models and providers.

Run it with: python -m app.worker
"""

import asyncio
import contextlib
import logging
import signal
from datetime import timedelta

from app.core.db import dispose_engine, get_sessionmaker
from app.core.settings import get_settings
from app.providers.parsing import DocumentParser
from app.providers.registry import get_document_parser, get_storage_backend
from app.providers.storage import StorageBackend
from app.services import jobs as jobs_service
from app.services.processing import RETRYABLE_ERRORS, mark_failed, process_job

logger = logging.getLogger(__name__)


async def process_one_batch(
    storage: StorageBackend, parser: DocumentParser, *, worker_id: str, batch_size: int
) -> int:
    """Claim and process up to `batch_size` jobs. Returns how many were handled.

    Each job gets its own transaction: one document failing to parse must not
    roll back the ones processed beside it.
    """
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    retry_delay = timedelta(seconds=settings.worker_retry_delay_seconds)

    async with sessionmaker() as session, session.begin():
        claimed = await jobs_service.claim(session, worker_id=worker_id, limit=batch_size)
        claimed_jobs = list(claimed)

    handled = 0
    for job in claimed_jobs:
        # The failure is recorded after the processing session is closed, not
        # from inside its except block: two sessions open at once on one job
        # buys nothing, and unwinding one while beginning the other is exactly
        # the kind of interleaving that bites under a shared connection.
        failure: str | None = None
        async with sessionmaker() as session:
            try:
                async with session.begin():
                    await session.merge(job)
                    await process_job(session, storage, parser, job=job)
                    await jobs_service.finish(session, tenant_id=job.tenant_id, job_id=job.id)
            except RETRYABLE_ERRORS as exc:
                failure = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                # An unexpected failure is still the job's failure, recorded by
                # type only: the message may quote the document.
                logger.exception("unexpected error while processing a job")
                failure = f"unexpected {type(exc).__name__}"

        if failure is not None:
            async with sessionmaker() as failure_session, failure_session.begin():
                await failure_session.merge(job)
                await mark_failed(failure_session, job=job, reason=failure, retry_delay=retry_delay)

        handled += 1

    return handled


async def run(stop: asyncio.Event | None = None) -> None:
    """Process jobs until asked to stop."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    stop = stop or asyncio.Event()

    storage = get_storage_backend()
    parser = get_document_parser()

    logger.info("worker started", extra={"worker_id": settings.worker_id})
    try:
        while not stop.is_set():
            handled = await process_one_batch(
                storage,
                parser,
                worker_id=settings.worker_id,
                batch_size=settings.worker_batch_size,
            )
            if handled == 0:
                # Nothing due: wait, but wake immediately when asked to stop.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_idle_sleep_seconds)
    finally:
        await dispose_engine()
        logger.info("worker stopped", extra={"worker_id": settings.worker_id})


def main() -> None:
    stop = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            # A stopping worker finishes the job in hand rather than abandoning
            # it half-written; the claim is released by the transaction either
            # way.
            loop.add_signal_handler(signal_name, stop.set)
        await run(stop)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
