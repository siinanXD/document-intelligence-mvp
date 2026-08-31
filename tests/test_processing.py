"""Processing a claimed job into a ready document.

Driven with a fake parser: the worker's behaviour - status transitions, chunk
replacement, content-duplicate detection, failure handling - is independent of
which parser produced the text, and testing it through Docling would make these
tests slow and dependent on downloaded models.
"""

from datetime import timedelta

import pytest

from app.models import Chunk, Document, DocumentStatus, JobStatus
from app.providers.parsing import ParsedChunk, ParsedDocument, ParsingError
from app.providers.storage import ObjectNotFoundError
from app.services import jobs as jobs_service
from app.services.normalization import content_hash
from app.services.processing import mark_failed, process_job

TEXT = "Service Agreement\n\nAcme agrees to pay EUR 1000 monthly."


class _FakeParser:
    """A parser with no dependencies, returning whatever the test needs."""

    def __init__(self, *, text=TEXT, chunks=None, error: Exception | None = None) -> None:
        self._text = text
        self._chunks = chunks
        self._error = error
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "fake"

    def supports(self, mime_type: str) -> bool:
        return True

    async def parse(self, *, filename, mime_type, content):
        self.calls.append({"filename": filename, "mime_type": mime_type, "size": len(content)})
        if self._error:
            raise self._error
        chunks = self._chunks
        if chunks is None:
            chunks = [
                ParsedChunk(ordinal=0, text="Service Agreement", section_title="Title"),
                ParsedChunk(
                    ordinal=1,
                    text="Acme agrees to pay EUR 1000 monthly.",
                    page_number=1,
                    section_title="Payment",
                    provenance={"bboxes": [{"page": 1, "l": 72, "t": 720, "r": 300, "b": 700}]},
                ),
            ]
        return ParsedDocument(text=self._text, chunks=list(chunks), serialized=b'{"schema":"fake"}')


@pytest.fixture
def parser():
    return _FakeParser()


@pytest.fixture
def storage(tmp_path):
    from app.providers.local_storage import LocalStorageBackend

    return LocalStorageBackend(tmp_path)


async def _queued(db_session, storage, tenant, make_document, content=b"%PDF-1.7 body"):
    document = await make_document(tenant)
    document.storage_key = f"{tenant.id}/{document.id}/doc.pdf"
    await db_session.flush()
    await storage.put(document.storage_key, content)
    job = await jobs_service.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)
    claimed = await jobs_service.claim(db_session, worker_id="worker-1")
    return document, claimed[0] if claimed else job


async def test_a_job_moves_the_document_to_ready(
    db_session, storage, parser, tenant, make_document
):
    document, job = await _queued(db_session, storage, tenant, make_document)

    outcome = await process_job(db_session, storage, parser, job=job)

    assert document.status is DocumentStatus.ready
    assert outcome.chunk_count == 2
    assert document.parser_name == "fake"
    assert document.content_hash == content_hash(TEXT)


async def test_chunks_are_stored_with_their_provenance(
    db_session, storage, parser, tenant, make_document
):
    document, job = await _queued(db_session, storage, tenant, make_document)

    await process_job(db_session, storage, parser, job=job)

    chunks = (
        await db_session.execute(Chunk.__table__.select().where(Chunk.document_id == document.id))
    ).all()
    assert len(chunks) == 2
    by_ordinal = {row.ordinal: row for row in chunks}
    assert by_ordinal[1].page_number == 1
    assert by_ordinal[1].section_title == "Payment"
    assert by_ordinal[1].source_metadata["bboxes"][0]["l"] == 72
    assert by_ordinal[0].source_id == f"{document.id}:00000"
    assert by_ordinal[0].tenant_id == tenant.id


async def test_the_parsed_representation_is_stored_for_reindexing(
    db_session, storage, parser, tenant, make_document
):
    document, job = await _queued(db_session, storage, tenant, make_document)

    await process_job(db_session, storage, parser, job=job)

    assert document.normalized_key
    assert await storage.get(document.normalized_key) == b'{"schema":"fake"}'


async def test_reprocessing_replaces_chunks_rather_than_adding_to_them(
    db_session, storage, parser, tenant, make_document
):
    document, job = await _queued(db_session, storage, tenant, make_document)
    await process_job(db_session, storage, parser, job=job)

    shorter = _FakeParser(chunks=[ParsedChunk(ordinal=0, text="Only one chunk now")])
    await process_job(db_session, storage, shorter, job=job)

    chunks = (
        await db_session.execute(Chunk.__table__.select().where(Chunk.document_id == document.id))
    ).all()
    assert len(chunks) == 1
    assert chunks[0].text == "Only one chunk now"


async def test_the_same_text_in_two_documents_is_a_content_duplicate(
    db_session, storage, parser, tenant, make_document
):
    """Different bytes, same words: found by content hash, not file hash."""
    first, first_job = await _queued(
        db_session, storage, tenant, make_document, content=b"%PDF-1.7 first bytes"
    )
    await process_job(db_session, storage, parser, job=first_job)

    second, second_job = await _queued(
        db_session, storage, tenant, make_document, content=b"%PDF-1.7 other bytes"
    )
    outcome = await process_job(db_session, storage, parser, job=second_job)

    assert first.file_hash != second.file_hash
    assert first.content_hash == second.content_hash
    assert outcome.content_duplicate_of == first.id


async def test_a_content_duplicate_never_points_across_tenants(
    db_session, storage, parser, tenant, other_tenant, make_document
):
    _, mine_job = await _queued(db_session, storage, tenant, make_document)
    await process_job(db_session, storage, parser, job=mine_job)

    _, theirs_job = await _queued(db_session, storage, other_tenant, make_document)
    outcome = await process_job(db_session, storage, parser, job=theirs_job)

    assert outcome.content_duplicate_of is None


async def test_a_parsing_failure_is_raised_for_the_caller_to_record(
    db_session, storage, tenant, make_document
):
    _, job = await _queued(db_session, storage, tenant, make_document)
    failing = _FakeParser(error=ParsingError("docling failed to convert the document"))

    with pytest.raises(ParsingError):
        await process_job(db_session, storage, failing, job=job)


async def test_a_missing_object_is_raised_for_the_caller_to_record(
    db_session, storage, parser, tenant, make_document
):
    document = await make_document(tenant)
    document.storage_key = "gone/missing.pdf"
    await db_session.flush()
    job = await jobs_service.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    with pytest.raises(ObjectNotFoundError):
        await process_job(db_session, storage, parser, job=job)


async def test_a_retryable_failure_leaves_the_document_queued(
    db_session, storage, tenant, make_document
):
    """While attempts remain the document is still going to be processed."""
    document, job = await _queued(db_session, storage, tenant, make_document)

    await mark_failed(
        db_session,
        job=job,
        worker_id="worker-1",
        reason="ParsingError: unreadable",
        retry_delay=timedelta(seconds=1),
    )

    assert document.status is DocumentStatus.queued
    refreshed = await jobs_service.get_job(db_session, tenant_id=tenant.id, job_id=job.id)
    assert refreshed.status is JobStatus.queued


async def test_the_last_failure_marks_the_document_failed(
    db_session, storage, tenant, make_document
):
    document = await make_document(tenant)
    await db_session.flush()
    await jobs_service.enqueue(
        db_session, tenant_id=tenant.id, document_id=document.id, max_attempts=1
    )
    claimed = await jobs_service.claim(db_session, worker_id="worker-1")

    await mark_failed(
        db_session,
        job=claimed[0],
        worker_id="worker-1",
        reason="ParsingError: unreadable",
        retry_delay=timedelta(seconds=1),
    )

    assert document.status is DocumentStatus.failed


async def test_a_failure_reason_carries_no_document_text(
    db_session, storage, tenant, make_document
):
    document, job = await _queued(db_session, storage, tenant, make_document)

    await mark_failed(
        db_session,
        job=job,
        worker_id="worker-1",
        reason="ParsingError: docling failed to convert the document (ValueError)",
        retry_delay=timedelta(seconds=1),
    )

    refreshed = await jobs_service.get_job(db_session, tenant_id=tenant.id, job_id=job.id)
    assert "Acme" not in refreshed.last_error
    assert "EUR" not in refreshed.last_error


async def test_the_parser_receives_the_stored_bytes(
    db_session, storage, parser, tenant, make_document
):
    await _queued(db_session, storage, tenant, make_document, content=b"%PDF-1.7 exact bytes")

    document = (
        await db_session.execute(Document.__table__.select().where(Document.tenant_id == tenant.id))
    ).first()
    job = await jobs_service.get_live_job(db_session, tenant_id=tenant.id, document_id=document.id)
    await process_job(db_session, storage, parser, job=job)

    assert parser.calls[0]["size"] == len(b"%PDF-1.7 exact bytes")
    assert parser.calls[0]["mime_type"] == "application/pdf"


async def test_a_deleted_document_is_not_processed(
    db_session, storage, parser, tenant, make_document
):
    from datetime import UTC, datetime

    document, job = await _queued(db_session, storage, tenant, make_document)
    document.deleted_at = datetime.now(UTC)
    await db_session.flush()

    outcome = await process_job(db_session, storage, parser, job=job)

    assert outcome.chunk_count == 0
    assert parser.calls == []
    assert document.status is not DocumentStatus.ready
