"""Idempotent document deletion across Postgres, object storage and Qdrant."""

import asyncio
import contextlib
import uuid

import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Chunk,
    Document,
    DocumentProfile,
    DocumentRelation,
    DocumentStatus,
    IngestionJob,
    RelationType,
    Tenant,
)
from app.providers.local_storage import LocalStorageBackend
from app.services import jobs as jobs_service
from app.services.deletion import delete_document
from app.services.indexing import index_document
from app.services.processing import process_job
from app.services.retrieval import search
from app.services.vector_store import DocumentVectorStore, VectorStoreService
from tests.test_indexing import _FakeEmbeddings
from tests.test_processing import _FakeParser


class _HoldDuringParse(_FakeParser):
    """Block inside parse so another connection can try to delete the row."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.allow = asyncio.Event()

    async def parse(self, *, filename, mime_type, content):
        self.started.set()
        await self.allow.wait()
        return await super().parse(filename=filename, mime_type=mime_type, content=content)


@pytest_asyncio.fixture
async def stores():
    client = AsyncQdrantClient(":memory:")
    chunks = VectorStoreService(client=client, collection="test_delete_chunks")
    documents = DocumentVectorStore(client=client, collection="test_delete_documents")
    yield chunks, documents
    await client.close()


async def _seed(
    db_session, storage, stores, tenant, make_document, *, file_hash: str | None = None
):
    chunk_store, document_store = stores
    document = await make_document(
        tenant,
        file_hash=file_hash,
        status=DocumentStatus.ready,
        filename="contract.pdf",
    )
    document.storage_key = f"{tenant.id}/{document.id}/contract.pdf"
    document.normalized_key = f"{document.storage_key}.docling.json"
    await db_session.flush()

    await storage.put(document.storage_key, b"%PDF-1.7 original")
    await storage.put(document.normalized_key, b'{"schema":"fake"}')

    db_session.add(
        Chunk(
            tenant_id=tenant.id,
            document_id=document.id,
            ordinal=0,
            text="Payment is due within thirty days.",
            source_id=f"{document.id}:00000",
        )
    )
    db_session.add(
        DocumentProfile(tenant_id=tenant.id, document_id=document.id, identifiers=["CASE-9"])
    )
    await db_session.flush()
    await jobs_service.enqueue(db_session, tenant_id=tenant.id, document_id=document.id)

    embeddings = _FakeEmbeddings()
    await index_document(
        db_session, embeddings, chunk_store, tenant_id=tenant.id, document_id=document.id
    )
    await document_store.ensure_collection(dimensions=embeddings.dimensions)
    await document_store.upsert(
        tenant_id=tenant.id, document_id=document.id, vector=[1.0] + [0.0] * 7
    )
    return document


async def _count(session, model, **filters):
    statement = select(func.count()).select_from(model)
    for column, value in filters.items():
        statement = statement.where(getattr(model, column) == value)
    return await session.scalar(statement)


async def test_delete_removes_storage_rows_and_vectors(
    db_session, tmp_path, stores, tenant, make_document
):
    storage = LocalStorageBackend(tmp_path)
    chunk_store, document_store = stores
    document = await _seed(db_session, storage, stores, tenant, make_document)
    other = await _seed(db_session, storage, stores, tenant, make_document, file_hash="b" * 64)
    db_session.add(
        DocumentRelation(
            tenant_id=tenant.id,
            source_document_id=document.id,
            target_document_id=other.id,
            relation_type=RelationType.related,
            score=0.8,
        )
    )
    await db_session.flush()

    outcome = await delete_document(
        db_session,
        storage,
        tenant_id=tenant.id,
        document_id=document.id,
        vector_store=chunk_store,
        document_vectors=document_store,
    )

    assert outcome is not None
    assert outcome.already_deleted is False
    assert document.deleted_at is not None
    assert await storage.exists(document.storage_key) is False
    assert await storage.exists(document.normalized_key) is False
    assert await _count(db_session, Chunk, document_id=document.id) == 0
    assert await _count(db_session, DocumentProfile, document_id=document.id) == 0
    assert await _count(db_session, IngestionJob, document_id=document.id) == 0
    remaining_relations = await db_session.scalar(
        select(func.count())
        .select_from(DocumentRelation)
        .where(
            or_(
                DocumentRelation.source_document_id == document.id,
                DocumentRelation.target_document_id == document.id,
            )
        )
    )
    assert remaining_relations == 0

    hits = await search(
        db_session,
        _FakeEmbeddings(),
        chunk_store,
        tenant_id=tenant.id,
        query="payment",
        limit=5,
    )
    assert {hit.document_id for hit in hits} == {other.id}

    similar = await document_store.similar(tenant_id=tenant.id, vector=[1.0] + [0.0] * 7, limit=5)
    assert [document_id for document_id, _ in similar] == [other.id]


async def test_delete_is_idempotent(db_session, tmp_path, stores, tenant, make_document):
    storage = LocalStorageBackend(tmp_path)
    chunk_store, document_store = stores
    document = await _seed(db_session, storage, stores, tenant, make_document)

    first = await delete_document(
        db_session,
        storage,
        tenant_id=tenant.id,
        document_id=document.id,
        vector_store=chunk_store,
        document_vectors=document_store,
    )
    second = await delete_document(
        db_session,
        storage,
        tenant_id=tenant.id,
        document_id=document.id,
        vector_store=chunk_store,
        document_vectors=document_store,
    )

    assert first is not None and first.already_deleted is False
    assert second is not None and second.already_deleted is True


async def test_another_tenant_cannot_delete_the_document(
    db_session, tmp_path, stores, tenant, other_tenant, make_document
):
    storage = LocalStorageBackend(tmp_path)
    chunk_store, document_store = stores
    document = await _seed(db_session, storage, stores, tenant, make_document)

    outcome = await delete_document(
        db_session,
        storage,
        tenant_id=other_tenant.id,
        document_id=document.id,
        vector_store=chunk_store,
        document_vectors=document_store,
    )

    assert outcome is None
    assert document.deleted_at is None
    assert await storage.exists(document.storage_key) is True
    assert await _count(db_session, Chunk, document_id=document.id) == 1


async def test_a_missing_document_deletes_as_absent(db_session, tmp_path, stores, tenant):
    storage = LocalStorageBackend(tmp_path)
    chunk_store, document_store = stores
    from uuid import uuid4

    outcome = await delete_document(
        db_session,
        storage,
        tenant_id=tenant.id,
        document_id=uuid4(),
        vector_store=chunk_store,
        document_vectors=document_store,
    )
    assert outcome is None


async def test_deleting_frees_the_hash_for_a_later_upload(
    db_session, tmp_path, stores, tenant, make_document
):
    from app.services import documents as documents_service

    storage = LocalStorageBackend(tmp_path)
    chunk_store, document_store = stores
    shared_hash = "e" * 64
    document = await _seed(
        db_session, storage, stores, tenant, make_document, file_hash=shared_hash
    )

    await delete_document(
        db_session,
        storage,
        tenant_id=tenant.id,
        document_id=document.id,
        vector_store=chunk_store,
        document_vectors=document_store,
    )

    assert (
        await documents_service.find_by_file_hash(
            db_session, tenant_id=tenant.id, file_hash=shared_hash
        )
        is None
    )
    replacement = await make_document(tenant, file_hash=shared_hash)
    assert replacement.id != document.id


async def test_delete_removes_output_an_in_flight_worker_commits(db_engine, tmp_path):
    """A DELETE that overlaps process_job must still erase what the worker wrote.

    Two real connections: the worker holds the document row through parse, the
    deleter blocks on FOR UPDATE, the worker then commits chunks and vectors,
    and the deleter must remove them rather than returning 204 over residue.
    """
    sessionmaker = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    storage = LocalStorageBackend(tmp_path)
    client = AsyncQdrantClient(":memory:")
    chunk_store = VectorStoreService(client=client, collection="test_delete_race_chunks")
    document_store = DocumentVectorStore(client=client, collection="test_delete_race_docs")
    embeddings = _FakeEmbeddings()
    parser = _HoldDuringParse()

    async with sessionmaker() as setup, setup.begin():
        tenant = Tenant(slug=f"del-race-{uuid.uuid4().hex[:8]}", name="Delete race")
        setup.add(tenant)
        await setup.flush()
        document = Document(
            tenant_id=tenant.id,
            filename="contract.pdf",
            mime_type="application/pdf",
            storage_key=f"{tenant.id}/{uuid.uuid4()}/contract.pdf",
            file_hash="a" * 64,
            status=DocumentStatus.queued,
        )
        setup.add(document)
        await setup.flush()
        await storage.put(document.storage_key, b"%PDF-1.7 race")
        job = await jobs_service.enqueue(setup, tenant_id=tenant.id, document_id=document.id)
        tenant_id = tenant.id
        document_id = document.id
        job_id = job.id
        storage_key = document.storage_key

    async def worker():
        async with sessionmaker() as session, session.begin():
            job_row = await session.get(IngestionJob, job_id)
            assert job_row is not None
            await process_job(
                session,
                storage,
                parser,
                job=job_row,
                embeddings=embeddings,
                vector_store=chunk_store,
                document_vectors=document_store,
            )

    async def deleter():
        await parser.started.wait()
        async with sessionmaker() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '8s'"))
            return await delete_document(
                session,
                storage,
                tenant_id=tenant_id,
                document_id=document_id,
                vector_store=chunk_store,
                document_vectors=document_store,
            )

    worker_task = asyncio.create_task(worker())
    try:
        await parser.started.wait()
        delete_task = asyncio.create_task(deleter())
        # Give the deleter time to block on FOR UPDATE while the worker still
        # holds the row through parse.
        await asyncio.sleep(0.05)
        parser.allow.set()
        await worker_task
        outcome = await delete_task

        assert outcome is not None
        assert outcome.already_deleted is False

        async with sessionmaker() as verify:
            document_row = await verify.get(Document, document_id)
            chunk_count = await verify.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
            )
            profile_count = await verify.scalar(
                select(func.count())
                .select_from(DocumentProfile)
                .where(DocumentProfile.document_id == document_id)
            )
            job_count = await verify.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .where(IngestionJob.document_id == document_id)
            )
            assert document_row is not None
            assert document_row.deleted_at is not None
            assert chunk_count == 0
            assert profile_count == 0
            assert job_count == 0
            normalized_key = document_row.normalized_key

        assert await storage.exists(storage_key) is False
        if normalized_key:
            assert await storage.exists(normalized_key) is False

        leftover_chunks = await chunk_store.search(
            tenant_id=tenant_id,
            vector=[1.0] + [0.0] * 7,
            limit=5,
            document_ids=[document_id],
        )
        leftover_docs = await document_store.similar(
            tenant_id=tenant_id, vector=[1.0] + [0.0] * 7, limit=5
        )
        assert leftover_chunks == []
        assert document_id not in {doc_id for doc_id, _ in leftover_docs}
    except Exception:
        parser.allow.set()
        raise
    finally:
        parser.allow.set()
        if not worker_task.done():
            with contextlib.suppress(Exception):
                await worker_task
        async with sessionmaker() as cleanup, cleanup.begin():
            tenant_row = await cleanup.get(Tenant, tenant_id)
            if tenant_row is not None:
                await cleanup.delete(tenant_row)
        await client.close()
