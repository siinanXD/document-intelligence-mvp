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
from app.services.qa import AskResult, GroundedAnswer, ask
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


class AskDecisions(BaseModel):
    """Why this answer looks the way it does. Identifiers and counts only."""

    retrieval_mode: str | None = None
    chunks_considered: int
    sources_accepted: int
    sources_rejected: int
    prompt_name: str | None = None
    prompt_version: str | None = None
    provider: str | None = None
    model: str | None = None
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    finish_reason: str | None = None


class AskResponse(BaseModel):
    answer: str
    has_sufficient_evidence: bool
    conflicting: bool
    sources: list[AnswerSource]
    considered: int
    decisions: AskDecisions

    @classmethod
    def of(cls, result: AskResult) -> "AskResponse":
        generation = result.generation
        retrieval = result.retrieval
        model_ids = []
        if generation is not None and isinstance(generation.content, GroundedAnswer):
            model_ids = list(generation.content.source_ids)
        accepted = {hit.source_id for hit in result.sources}
        rejected = max(0, len(model_ids) - len(accepted))
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
            decisions=AskDecisions(
                retrieval_mode=None if retrieval is None else retrieval.mode,
                chunks_considered=result.considered,
                sources_accepted=len(result.sources),
                sources_rejected=rejected,
                prompt_name=None if generation is None else generation.prompt_name,
                prompt_version=None if generation is None else generation.prompt_version,
                provider=None if generation is None else generation.provider,
                model=None if generation is None else generation.model,
                latency_ms=None if generation is None else generation.latency_ms,
                input_tokens=None if generation is None else generation.input_tokens,
                output_tokens=None if generation is None else generation.output_tokens,
                estimated_cost_usd=None if generation is None else generation.estimated_cost_usd,
                finish_reason=None if generation is None else generation.finish_reason,
            ),
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
