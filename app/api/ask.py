"""Grounded question answering over a tenant's documents."""

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import SessionDep, TenantDep
from app.core.settings import get_settings
from app.providers.registry import (
    ProviderConfigurationError,
    get_embedding_provider,
    get_llm_provider,
)
from app.services.qa import AskResult, ask
from app.services.vector_store import VectorStoreError, VectorStoreService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[uuid.UUID] | None = None
    limit: int | None = Field(default=None, ge=1)


class AnswerSource(BaseModel):
    source_id: str
    document_id: uuid.UUID
    filename: str
    page_number: int | None
    section_title: str | None
    text: str
    score: float


class AskResponse(BaseModel):
    answer: str
    has_sufficient_evidence: bool
    conflicting: bool
    sources: list[AnswerSource]
    considered: int

    @classmethod
    def of(cls, result: AskResult) -> "AskResponse":
        return cls(
            answer=result.answer,
            has_sufficient_evidence=result.has_sufficient_evidence,
            conflicting=result.conflicting,
            considered=result.considered,
            sources=[
                AnswerSource(
                    source_id=hit.source_id,
                    document_id=hit.document_id,
                    filename=hit.document_filename,
                    page_number=hit.page_number,
                    section_title=hit.section_title,
                    text=hit.text,
                    score=hit.score,
                )
                for hit in result.sources
            ],
        )


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={
        400: {"description": "The tenant header is missing or malformed"},
        503: {"description": "The answering backend is unavailable"},
    },
)
async def ask_documents(session: SessionDep, tenant: TenantDep, request: AskRequest) -> AskResponse:
    """Answer a question from this tenant's documents, citing what it used.

    An answer with `has_sufficient_evidence` false is a successful response, not
    an error: "the documents do not say" is a real answer, and the alternative
    is a confident guess.
    """
    settings = get_settings()
    limit = min(request.limit or settings.ask_default_limit, settings.ask_max_limit)

    try:
        embeddings = get_embedding_provider()
        llm = get_llm_provider()
    except ProviderConfigurationError as exc:
        logger.error("ask unavailable: an AI provider is not configured")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "answering is not configured"
        ) from exc

    try:
        result = await ask(
            session,
            embeddings,
            llm,
            VectorStoreService(),
            tenant_id=tenant.id,
            question=request.question,
            limit=limit,
            document_ids=request.document_ids,
        )
    except VectorStoreError as exc:
        logger.warning("ask failed against the vector store")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "answering is temporarily unavailable"
        ) from exc

    return AskResponse.of(result)
