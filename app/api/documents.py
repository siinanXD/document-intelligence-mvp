"""Document upload and retrieval.

Routes stay thin: they validate the request, call a service and shape the
response. No route touches storage, Qdrant or the ORM query layer directly.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel

from app.api.dependencies import SessionDep, StorageDep, TenantDep
from app.core.settings import get_settings
from app.models import Document
from app.providers.registry import ProviderConfigurationError, get_embedding_provider
from app.services import documents as documents_service
from app.services import lexical
from app.services import relations as relations_service
from app.services.deletion import delete_document
from app.services.ingestion import ingest_upload
from app.services.reindexing import ReindexError, reindex_document, reindex_tenant
from app.services.uploads import EmptyFile, FileTooLarge, UnsupportedFileType, validate_upload
from app.services.vector_store import DocumentVectorStore, VectorStoreError, VectorStoreService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])
lifecycle_router = APIRouter(tags=["documents"])


class DocumentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str
    file_hash: str
    status: str
    title: str | None
    document_type: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, document: Document) -> "DocumentResponse":
        return cls(
            id=document.id,
            filename=document.filename,
            mime_type=document.mime_type,
            file_hash=document.file_hash,
            status=document.status.value,
            title=document.title,
            document_type=document.document_type,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class UploadResponse(BaseModel):
    document: DocumentResponse
    duplicate: bool


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "These exact bytes are already stored for this tenant"},
        400: {"description": "The upload is empty or the tenant header is malformed"},
        413: {"description": "The upload exceeds the configured size limit"},
        415: {"description": "The file type is not supported"},
    },
)
async def upload_document(
    session: SessionDep,
    tenant: TenantDep,
    storage: StorageDep,
    response: Response,
    file: Annotated[UploadFile, File()],
) -> UploadResponse:
    settings = get_settings()
    content = await file.read()

    try:
        upload = validate_upload(
            filename=file.filename or "upload",
            declared_mime_type=file.content_type,
            content=content,
            max_bytes=settings.max_upload_bytes,
        )
    except EmptyFile as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileTooLarge as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    except UnsupportedFileType as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    result = await ingest_upload(
        session, storage, tenant_id=tenant.id, upload=upload, content=content
    )

    # A duplicate created nothing, so it is not a 201.
    if result.is_duplicate:
        response.status_code = status.HTTP_200_OK

    return UploadResponse(
        document=DocumentResponse.of(result.document), duplicate=result.is_duplicate
    )


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    session: SessionDep,
    tenant: TenantDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[DocumentResponse]:
    found = await documents_service.list_documents(
        session, tenant_id=tenant.id, limit=limit, offset=offset
    )
    return [DocumentResponse.of(document) for document in found]


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    responses={404: {"description": "No such document for this tenant"}},
)
async def get_document(
    session: SessionDep, tenant: TenantDep, document_id: uuid.UUID
) -> DocumentResponse:
    document = await documents_service.get_document(
        session, tenant_id=tenant.id, document_id=document_id
    )
    if document is None:
        # Another tenant's document is absent, not forbidden: a 403 would
        # confirm that the id exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return DocumentResponse.of(document)


class SourceResponse(BaseModel):
    """A citation resolved back to the passage it points at."""

    source_id: str
    document_id: uuid.UUID
    filename: str
    ordinal: int
    text: str
    page_number: int | None
    section_title: str | None
    source_metadata: dict


@router.get(
    "/{document_id}/sources/{source_id}",
    response_model=SourceResponse,
    responses={404: {"description": "No such source for this tenant and document"}},
)
async def resolve_source(
    session: SessionDep, tenant: TenantDep, document_id: uuid.UUID, source_id: str
) -> SourceResponse:
    """Resolve a source id from an answer or a search result.

    Scoped to the tenant and the named document: an id belonging to another
    tenant, or to a different document, is absent rather than forbidden.
    """
    hit = await lexical.resolve_source(
        session, tenant_id=tenant.id, document_id=document_id, source_id=source_id
    )
    if hit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")

    return SourceResponse(
        source_id=hit.source_id,
        document_id=hit.document_id,
        filename=hit.document_filename,
        ordinal=hit.ordinal,
        text=hit.text,
        page_number=hit.page_number,
        section_title=hit.section_title,
        source_metadata=hit.source_metadata,
    )


class RelationTarget(BaseModel):
    document_id: uuid.UUID
    filename: str
    title: str | None
    document_type: str | None


class RelationResponse(BaseModel):
    """One relation, and the signals that produced it."""

    relation_type: str
    score: float | None
    # Which signals fired, and what they saw. A relation nobody can interrogate
    # is a relation nobody will trust.
    reason: dict
    target: RelationTarget


@router.get(
    "/{document_id}/relations",
    response_model=list[RelationResponse],
    responses={404: {"description": "No such document for this tenant"}},
)
async def list_relations(
    session: SessionDep, tenant: TenantDep, document_id: uuid.UUID
) -> list[RelationResponse]:
    """How this document relates to the tenant's other documents."""
    document = await documents_service.get_document(
        session, tenant_id=tenant.id, document_id=document_id
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")

    found = await relations_service.list_relations(
        session, tenant_id=tenant.id, document_id=document_id
    )
    return [
        RelationResponse(
            relation_type=relation.relation_type.value,
            score=relation.score,
            reason=relation.reason or {},
            target=RelationTarget(
                document_id=target.id,
                filename=target.filename,
                title=target.title,
                document_type=target.document_type,
            ),
        )
        for relation, target in found
    ]


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        204: {"description": "The document is gone, or was already gone"},
        404: {"description": "No such document for this tenant"},
        503: {"description": "The vector store could not complete the delete"},
    },
)
async def remove_document(
    session: SessionDep,
    tenant: TenantDep,
    storage: StorageDep,
    document_id: uuid.UUID,
) -> Response:
    """Idempotent delete: storage, derived rows and both vector collections.

    Another tenant's document is absent, not forbidden. Repeating the call
    after a successful delete is still 204.
    """
    try:
        outcome = await delete_document(
            session,
            storage,
            tenant_id=tenant.id,
            document_id=document_id,
            vector_store=VectorStoreService(),
            document_vectors=DocumentVectorStore(),
        )
    except VectorStoreError as exc:
        logger.warning(
            "document delete failed against the vector store",
            extra={"tenant_id": str(tenant.id), "document_id": str(document_id)},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "deletion is temporarily unavailable"
        ) from exc

    if outcome is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ReindexResponse(BaseModel):
    document_id: uuid.UUID
    indexed: int
    stale_before: bool


class TenantReindexResponse(BaseModel):
    tenant_id: uuid.UUID
    reindexed: int
    skipped: int
    failed: int


@router.post(
    "/{document_id}/reindex",
    response_model=ReindexResponse,
    responses={
        404: {"description": "No such document for this tenant"},
        409: {"description": "The document has no stored derived data to reindex"},
        503: {"description": "The embedding provider or vector store is unavailable"},
    },
)
async def reindex_one_document(
    session: SessionDep, tenant: TenantDep, document_id: uuid.UUID
) -> ReindexResponse:
    """Rebuild this document's vectors from stored chunks. No re-upload."""
    try:
        embeddings = get_embedding_provider()
    except ProviderConfigurationError as exc:
        logger.error("reindex unavailable: embedding provider not configured")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "reindex is not configured"
        ) from exc

    try:
        outcome = await reindex_document(
            session,
            embeddings,
            VectorStoreService(),
            tenant_id=tenant.id,
            document_id=document_id,
            document_vectors=DocumentVectorStore(),
        )
    except ReindexError as exc:
        message = str(exc)
        if "no longer exists" in message:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found") from exc
        raise HTTPException(status.HTTP_409_CONFLICT, "document has no stored chunks") from exc
    except VectorStoreError as exc:
        logger.warning(
            "reindex failed against the vector store",
            extra={"tenant_id": str(tenant.id), "document_id": str(document_id)},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "reindex is temporarily unavailable"
        ) from exc

    return ReindexResponse(
        document_id=outcome.document_id,
        indexed=outcome.indexed,
        stale_before=outcome.stale_before,
    )


@lifecycle_router.post(
    "/reindex",
    response_model=TenantReindexResponse,
    responses={
        503: {"description": "The embedding provider or vector store is unavailable"},
    },
)
async def reindex_tenant_documents(session: SessionDep, tenant: TenantDep) -> TenantReindexResponse:
    """Rebuild vectors for every live document of this tenant."""
    try:
        embeddings = get_embedding_provider()
    except ProviderConfigurationError as exc:
        logger.error("reindex unavailable: embedding provider not configured")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "reindex is not configured"
        ) from exc

    try:
        outcome = await reindex_tenant(
            session,
            embeddings,
            VectorStoreService(),
            tenant_id=tenant.id,
            document_vectors=DocumentVectorStore(),
        )
    except VectorStoreError as exc:
        logger.warning(
            "tenant reindex failed against the vector store",
            extra={"tenant_id": str(tenant.id)},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "reindex is temporarily unavailable"
        ) from exc

    return TenantReindexResponse(
        tenant_id=tenant.id,
        reindexed=outcome.reindexed,
        skipped=outcome.skipped,
        failed=outcome.failed,
    )
