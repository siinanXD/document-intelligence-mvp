"""Load a golden dataset into the real ingestion and indexing path."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.cases import DatasetDocument, EvalDataset, GenerationDataset
from app.evaluation.formats import build_file
from app.evaluation.parser import EvaluationParser
from app.models import Chunk, Document, Tenant
from app.providers.base import EmbeddingProvider
from app.providers.storage import StorageBackend
from app.services import jobs as jobs_service
from app.services.ingestion import ingest_upload
from app.services.processing import process_job
from app.services.uploads import validate_upload
from app.services.vector_store import VectorStoreService


async def ensure_tenant(session: AsyncSession, *, key: str) -> Tenant:
    slug = f"eval-{key}"
    existing = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalars().first()
    if existing is not None:
        return existing
    tenant = Tenant(slug=slug, name=f"Evaluation {key}", id=uuid.uuid4())
    session.add(tenant)
    await session.flush()
    return tenant


async def ingest_dataset(
    session: AsyncSession,
    storage: StorageBackend,
    dataset: EvalDataset | GenerationDataset,
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    *,
    parser: EvaluationParser | None = None,
) -> dict[str, dict[str, Document]]:
    """Ingest every fixture. Returns {tenant_key: {filename: Document}}."""
    parser = parser or EvaluationParser()
    tenants: dict[str, Tenant] = {}
    documents: dict[str, dict[str, Document]] = {}
    for spec in dataset.documents:
        tenant = tenants.get(spec.tenant)
        if tenant is None:
            tenant = await ensure_tenant(session, key=spec.tenant)
            tenants[spec.tenant] = tenant
            documents[spec.tenant] = {}
        document = await _ingest_one(
            session,
            storage,
            spec,
            dataset=dataset,
            tenant=tenant,
            embeddings=embeddings,
            vector_store=vector_store,
            parser=parser,
        )
        documents[spec.tenant][spec.filename] = document
    return documents


async def _ingest_one(
    session: AsyncSession,
    storage: StorageBackend,
    spec: DatasetDocument,
    *,
    dataset: EvalDataset | GenerationDataset,
    tenant: Tenant,
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    parser: EvaluationParser,
) -> Document:
    source_path = dataset.root / spec.source
    source = source_path.read_text(encoding="utf-8")
    content, mime = build_file(spec.format, source)
    upload = validate_upload(
        filename=spec.filename,
        declared_mime_type=mime,
        content=content,
        max_bytes=10_000_000,
    )
    result = await ingest_upload(
        session, storage, tenant_id=tenant.id, upload=upload, content=content
    )
    job = await jobs_service.get_live_job(
        session, tenant_id=tenant.id, document_id=result.document.id
    )
    if job is None:
        raise RuntimeError(f"ingest produced no job for {spec.filename}")
    await process_job(
        session,
        storage,
        parser,
        job=job,
        embeddings=embeddings,
        vector_store=vector_store,
    )
    await session.flush()
    return result.document


async def resolve_judgment(
    session: AsyncSession, *, tenant_id, document: Document, contains: str
) -> list[str]:
    rows = (
        (
            await session.execute(
                select(Chunk.source_id).where(
                    Chunk.tenant_id == tenant_id,
                    Chunk.document_id == document.id,
                    Chunk.text.contains(contains),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise RuntimeError(
            f"relevance judgment matched no chunk in {document.filename}: {contains!r}"
        )
    return [str(source_id) for source_id in rows]
