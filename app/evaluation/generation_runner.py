"""Run generation-v1 cases through the real `ask` service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.cases import GenerationCase, GenerationDataset
from app.evaluation.generation_metrics import (
    GenerationCaseScore,
    GenerationSummary,
    aggregate_generation_scores,
    score_generation_case,
)
from app.evaluation.ingest import resolve_judgment
from app.evaluation.judge import JudgeScore, NullJudge
from app.models import Chunk, Document
from app.providers.base import EmbeddingProvider, LLMProvider
from app.services.qa import ask
from app.services.vector_store import VectorStoreService

EVAL_ASK_LIMIT = 8

# Live runs stop before exceeding these unless the caller overrides.
LIVE_DEFAULT_MAX_CASES = 8
LIVE_DEFAULT_MAX_COST_USD = 0.50


@dataclass(frozen=True)
class GenerationRunResult:
    scores: list[GenerationCaseScore]
    summary: GenerationSummary
    stopped_reason: str | None = None


async def run_generation_dataset(
    session: AsyncSession,
    dataset: GenerationDataset,
    documents: dict[str, dict[str, Document]],
    embeddings: EmbeddingProvider,
    llm: LLMProvider,
    vector_store: VectorStoreService,
    *,
    judge: Any | None = None,
    max_cases: int | None = None,
    max_cost_usd: float | None = None,
    limit: int = EVAL_ASK_LIMIT,
) -> GenerationRunResult:
    judge = judge or NullJudge()
    scores: list[GenerationCaseScore] = []
    cost_sum = 0.0
    saw_cost = False
    stopped_reason: str | None = None
    for index, case in enumerate(dataset.cases):
        if max_cases is not None and index >= max_cases:
            stopped_reason = "max_cases"
            break
        if max_cost_usd is not None and saw_cost and cost_sum >= max_cost_usd:
            stopped_reason = "max_cost_usd"
            break
        score = await _run_generation_case(
            session,
            case,
            documents=documents,
            embeddings=embeddings,
            llm=llm,
            vector_store=vector_store,
            judge=judge,
            limit=limit,
        )
        scores.append(score)
        if score.estimated_cost_usd is not None:
            saw_cost = True
            cost_sum += score.estimated_cost_usd
    return GenerationRunResult(
        scores=scores,
        summary=aggregate_generation_scores(scores),
        stopped_reason=stopped_reason,
    )


async def _run_generation_case(
    session: AsyncSession,
    case: GenerationCase,
    *,
    documents: dict[str, dict[str, Document]],
    embeddings: EmbeddingProvider,
    llm: LLMProvider,
    vector_store: VectorStoreService,
    judge: Any,
    limit: int,
) -> GenerationCaseScore:
    tenant_docs = documents[case.tenant]
    tenant_id = next(iter(tenant_docs.values())).tenant_id
    relevant: set[str] = set()
    for judgment in case.relevant:
        if judgment.document not in tenant_docs:
            raise RuntimeError(f"{case.id}: relevant document {judgment.document} was not ingested")
        relevant.update(
            await resolve_judgment(
                session,
                tenant_id=tenant_id,
                document=tenant_docs[judgment.document],
                contains=judgment.contains,
            )
        )

    result = await ask(
        session,
        embeddings,
        llm,
        vector_store,
        tenant_id=tenant_id,
        question=case.query,
        limit=limit,
    )

    retrieved_ids = {
        source.source_id for source in (result.retrieval.sources if result.retrieval else ())
    }
    cited_filenames = {hit.source_id: hit.document_filename for hit in result.sources}
    owned_filenames = {document.filename for document in tenant_docs.values()}
    resolvable = await _resolvable_source_ids(
        session, tenant_id=tenant_id, source_ids=[hit.source_id for hit in result.sources]
    )
    judge_score: JudgeScore = await judge.score(
        question=case.query,
        result=result,
        expected_facts=case.expected_facts,
        forbidden_claims=case.forbidden_claims,
    )
    return score_generation_case(
        case=case,
        result=result,
        relevant_ids=relevant,
        retrieved_ids=retrieved_ids,
        resolvable_ids=resolvable,
        owned_filenames=owned_filenames,
        cited_filenames=cited_filenames,
        judge_groundedness=judge_score.groundedness,
        judge_completeness=judge_score.completeness,
        judge_error=judge_score.error,
    )


async def _resolvable_source_ids(
    session: AsyncSession, *, tenant_id, source_ids: list[str]
) -> set[str]:
    if not source_ids:
        return set()
    rows = (
        (
            await session.execute(
                select(Chunk.source_id).where(
                    Chunk.tenant_id == tenant_id,
                    Chunk.source_id.in_(source_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    return {str(source_id) for source_id in rows}
