"""The worker loop: claim, process, record, keep going.

Driven with a fake parser, so what is under test is the loop - a transaction
per job, failure handling, shutdown - rather than Docling.

Unlike the other database tests these do not run inside a rolled-back
transaction: the worker opens its own sessions and commits, which is the
behaviour being tested. Each test creates its own tenant and deletes it
afterwards, so nothing leaks between them.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Document, DocumentStatus, IngestionJob, JobStatus, Tenant
from app.providers.parsing import ParsingError
from app.services import jobs as jobs_service
from app.worker import process_one_batch, run
from tests.test_processing import _FakeParser


@pytest.fixture
def storage(tmp_path):
    from app.providers.local_storage import LocalStorageBackend

    return LocalStorageBackend(tmp_path)


@pytest_asyncio.fixture
async def sessions(db_engine, monkeypatch):
    """Real committing sessions, which is what the worker uses in production."""
    sessionmaker = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.worker.get_sessionmaker", lambda: sessionmaker)
    return sessionmaker


@pytest_asyncio.fixture
async def workspace(sessions, storage):
    """A tenant of this test's own, removed afterwards."""
    async with sessions() as session:
        tenant = Tenant(slug=f"worker-{uuid.uuid4().hex[:8]}", name="Worker")
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    try:
        yield tenant_id
    finally:
        async with sessions() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await session.commit()


async def _queue(sessions, storage, tenant_id, count=1) -> list[uuid.UUID]:
    ids = []
    async with sessions() as session:
        for index in range(count):
            document_id = uuid.uuid4()
            key = f"{tenant_id}/{document_id}/doc.pdf"
            session.add(
                Document(
                    id=document_id,
                    tenant_id=tenant_id,
                    filename=f"doc-{index}.pdf",
                    mime_type="application/pdf",
                    storage_key=key,
                    file_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    status=DocumentStatus.queued,
                )
            )
            await session.flush()
            await storage.put(key, b"%PDF-1.7 body")
            await jobs_service.enqueue(session, tenant_id=tenant_id, document_id=document_id)
            ids.append(document_id)
        await session.commit()
    return ids


async def _status_of(sessions, document_id) -> DocumentStatus:
    async with sessions() as session:
        return (
            await session.execute(select(Document.status).where(Document.id == document_id))
        ).scalar_one()


async def _job_of(sessions, document_id):
    async with sessions() as session:
        return (
            await session.execute(
                select(IngestionJob.status, IngestionJob.last_error).where(
                    IngestionJob.document_id == document_id
                )
            )
        ).first()


async def test_a_batch_processes_a_queued_document(sessions, storage, workspace):
    (document_id,) = await _queue(sessions, storage, workspace)

    handled = await process_one_batch(storage, _FakeParser(), worker_id="worker-1", batch_size=5)

    assert handled == 1
    assert await _status_of(sessions, document_id) is DocumentStatus.ready


async def test_an_empty_queue_handles_nothing(sessions, storage, workspace):
    handled = await process_one_batch(storage, _FakeParser(), worker_id="worker-1", batch_size=5)

    assert handled == 0


async def test_a_batch_processes_several_documents(sessions, storage, workspace):
    ids = await _queue(sessions, storage, workspace, count=3)

    handled = await process_one_batch(storage, _FakeParser(), worker_id="worker-1", batch_size=3)

    assert handled == 3
    for document_id in ids:
        assert await _status_of(sessions, document_id) is DocumentStatus.ready


async def test_the_batch_size_bounds_what_is_claimed(sessions, storage, workspace):
    await _queue(sessions, storage, workspace, count=3)

    handled = await process_one_batch(storage, _FakeParser(), worker_id="worker-1", batch_size=2)

    assert handled == 2


async def test_a_failing_document_is_recorded_and_the_worker_survives(sessions, storage, workspace):
    (document_id,) = await _queue(sessions, storage, workspace)
    failing = _FakeParser(error=ParsingError("docling failed to convert the document"))

    handled = await process_one_batch(storage, failing, worker_id="worker-1", batch_size=5)

    assert handled == 1
    job = await _job_of(sessions, document_id)
    assert job.status is JobStatus.queued
    assert "ParsingError" in job.last_error
    # Attempts remain, so the document is still going to be processed.
    assert await _status_of(sessions, document_id) is DocumentStatus.queued


async def test_an_unexpected_error_is_recorded_by_type_only(sessions, storage, workspace):
    """A surprise must not take the worker down, nor leak the document."""
    (document_id,) = await _queue(sessions, storage, workspace)

    class _Exploding(_FakeParser):
        async def parse(self, *, filename, mime_type, content):
            raise RuntimeError("Acme agrees to pay EUR 1000 monthly.")

    handled = await process_one_batch(storage, _Exploding(), worker_id="worker-1", batch_size=5)

    assert handled == 1
    job = await _job_of(sessions, document_id)
    assert job.last_error == "unexpected RuntimeError"
    assert "Acme" not in job.last_error
    assert "EUR" not in job.last_error


async def test_one_failure_does_not_roll_back_the_documents_beside_it(sessions, storage, workspace):
    """Each job gets its own transaction."""
    ids = await _queue(sessions, storage, workspace, count=2)

    class _FailsTheFirst(_FakeParser):
        def __init__(self):
            super().__init__()
            self.seen = 0

        async def parse(self, *, filename, mime_type, content):
            self.seen += 1
            if self.seen == 1:
                raise ParsingError("the first one is unreadable")
            return await super().parse(filename=filename, mime_type=mime_type, content=content)

    await process_one_batch(storage, _FailsTheFirst(), worker_id="worker-1", batch_size=2)

    statuses = {await _status_of(sessions, document_id) for document_id in ids}
    assert statuses == {DocumentStatus.ready, DocumentStatus.queued}


async def test_a_processed_document_is_not_claimed_again(sessions, storage, workspace):
    await _queue(sessions, storage, workspace)
    await process_one_batch(storage, _FakeParser(), worker_id="worker-1", batch_size=5)

    assert await process_one_batch(storage, _FakeParser(), worker_id="worker-2", batch_size=5) == 0


async def test_the_loop_stops_when_asked(sessions, storage, monkeypatch):
    from qdrant_client import AsyncQdrantClient

    from app.services.vector_store import DocumentVectorStore, VectorStoreService
    from tests.test_indexing import _FakeEmbeddings
    from tests.test_qa import _FakeLLM

    monkeypatch.setattr("app.worker.get_storage_backend", lambda: storage)
    monkeypatch.setattr("app.worker.get_document_parser", _FakeParser)
    monkeypatch.setattr("app.worker.get_embedding_provider", _FakeEmbeddings)
    monkeypatch.setattr("app.worker.get_llm_provider", _FakeLLM)
    monkeypatch.setattr(
        "app.worker.VectorStoreService",
        lambda: VectorStoreService(client=AsyncQdrantClient(":memory:"), collection="loop"),
    )
    monkeypatch.setattr(
        "app.worker.DocumentVectorStore",
        lambda: DocumentVectorStore(
            client=AsyncQdrantClient(":memory:"), collection="loop_documents"
        ),
    )

    async def _noop():
        return None

    monkeypatch.setattr("app.worker.dispose_engine", _noop)
    monkeypatch.setenv("WORKER_IDLE_SLEEP_SECONDS", "0.01")

    stop = asyncio.Event()
    task = asyncio.create_task(run(stop))
    await asyncio.sleep(0.05)
    stop.set()

    await asyncio.wait_for(task, timeout=5)


async def test_the_worker_refuses_to_start_without_an_embedding_provider(
    sessions, storage, monkeypatch
):
    """Parsing without indexing would mark documents ready that answer nothing.

    Failing at startup is the loud version of that, and the only one an
    operator notices.
    """
    from app.providers.registry import ProviderConfigurationError

    monkeypatch.setattr("app.worker.get_storage_backend", lambda: storage)
    monkeypatch.setattr("app.worker.get_document_parser", _FakeParser)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError):
        await run(asyncio.Event())
