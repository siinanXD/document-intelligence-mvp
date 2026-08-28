"""Concurrency regression tests for the upload transaction."""

import asyncio
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Document, IngestionJob, Tenant
from app.providers.local_storage import LocalStorageBackend
from app.services.ingestion import ingest_upload
from app.services.uploads import validate_upload

PDF = b"%PDF-1.7\nconcurrent upload"


class _BlockingStorage(LocalStorageBackend):
    def __init__(self, root):
        super().__init__(root)
        self.put_started = asyncio.Event()
        self.allow_put = asyncio.Event()

    async def put(self, key, data, content_type=None):
        self.put_started.set()
        await self.allow_put.wait()
        await super().put(key, data, content_type)


async def test_concurrent_identical_uploads_converge_on_one_document(db_engine, tmp_path):
    sessionmaker = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    storage = _BlockingStorage(tmp_path)
    upload = validate_upload(
        filename="contract.pdf",
        declared_mime_type="application/pdf",
        content=PDF,
        max_bytes=1024,
    )

    async with sessionmaker() as setup:
        tenant = Tenant(slug=f"upload-race-{uuid.uuid4().hex[:8]}", name="Upload race")
        setup.add(tenant)
        await setup.commit()
        tenant_id = tenant.id

    async def run_upload():
        async with sessionmaker() as session, session.begin():
            return await ingest_upload(
                session,
                storage,
                tenant_id=tenant_id,
                upload=upload,
                content=PDF,
            )

    first_task = asyncio.create_task(run_upload())
    await storage.put_started.wait()

    second_task = asyncio.create_task(run_upload())
    # Give the second transaction a chance to reach the advisory lock while
    # the first still owns it. Its correctness must not depend on timing after
    # the first transaction commits.
    await asyncio.sleep(0.05)
    storage.allow_put.set()

    first, second = await asyncio.gather(first_task, second_task)

    try:
        assert first.is_duplicate is False
        assert second.is_duplicate is True
        assert second.document.id == first.document.id

        async with sessionmaker() as verify:
            document_count = await verify.scalar(
                select(func.count()).select_from(Document).where(Document.tenant_id == tenant_id)
            )
            job_count = await verify.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .where(IngestionJob.tenant_id == tenant_id)
            )

        assert document_count == 1
        assert job_count == 1
    finally:
        async with sessionmaker() as cleanup, cleanup.begin():
            tenant = await cleanup.get(Tenant, tenant_id)
            if tenant is not None:
                await cleanup.delete(tenant)
