"""Semantic search.

The route validates and delegates: it never builds a Qdrant filter, never
touches the client, and never decides what a tenant may see - the retrieval
service does all three, so the tenant filter cannot be forgotten here.
"""

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import SessionDep, TenantDep
from app.core.settings import get_settings
from app.providers.registry import (
    ProviderConfigurationError,
    get_embedding_provider,
    get_reranker,
)
from app.services import lexical
from app.services.retrieval import SearchHit, search
from app.services.vector_store import VectorStoreError, VectorStoreService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    document_ids: list[uuid.UUID] | None = None
    limit: int | None = Field(default=None, ge=1)
    # Semantic finds passages that mean the same thing; lexical finds passages
    # that contain the literal text. An identifier belongs to the second.
    mode: Literal["semantic", "lexical"] = "semantic"


class SearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    source_id: str
    text: str
    score: float
    ordinal: int
    page_number: int | None
    section_title: str | None
    source_metadata: dict

    @classmethod
    def of_lexical(cls, hit: lexical.LexicalHit) -> "SearchResult":
        return cls(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            filename=hit.document_filename,
            source_id=hit.source_id,
            text=hit.text,
            score=hit.rank,
            ordinal=hit.ordinal,
            page_number=hit.page_number,
            section_title=hit.section_title,
            source_metadata=hit.source_metadata,
        )

    @classmethod
    def of(cls, hit: SearchHit) -> "SearchResult":
        return cls(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            filename=hit.document_filename,
            source_id=hit.source_id,
            text=hit.text,
            score=hit.score,
            ordinal=hit.ordinal,
            page_number=hit.page_number,
            section_title=hit.section_title,
            source_metadata=hit.source_metadata,
        )


class SearchResponse(BaseModel):
    mode: str
    results: list[SearchResult]


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"description": "The tenant header is missing or malformed"},
        503: {"description": "The search backend is unavailable"},
    },
)
async def search_documents(
    session: SessionDep, tenant: TenantDep, request: SearchRequest
) -> SearchResponse:
    settings = get_settings()
    limit = min(request.limit or settings.search_default_limit, settings.search_max_limit)

    if request.mode == "lexical":
        # No embedding provider needed, and no vector store: lexical search is
        # answered entirely by PostgreSQL, so it keeps working when they do not.
        hits = await lexical.search(
            session,
            tenant_id=tenant.id,
            query=request.query,
            limit=limit,
            document_ids=request.document_ids,
        )
        return SearchResponse(
            mode="lexical", results=[SearchResult.of_lexical(hit) for hit in hits]
        )

    try:
        embeddings = get_embedding_provider()
        reranker = get_reranker()
    except ProviderConfigurationError as exc:
        # A missing key is an operator problem, not the caller's fault, and the
        # message must not name the variable's value.
        logger.error("search unavailable: a search provider is not configured")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "search is not configured"
        ) from exc

    try:
        hits = await search(
            session,
            embeddings,
            VectorStoreService(),
            tenant_id=tenant.id,
            query=request.query,
            limit=limit,
            document_ids=request.document_ids,
            reranker=reranker,
        )
    except VectorStoreError as exc:
        logger.warning("search failed against the vector store")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "search is temporarily unavailable"
        ) from exc

    return SearchResponse(mode="semantic", results=[SearchResult.of(hit) for hit in hits])
