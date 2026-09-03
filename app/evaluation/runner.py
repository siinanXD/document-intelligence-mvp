"""Run the golden retrieval cases through the real search services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.cases import EvalCase, EvalDataset
from app.evaluation.ingest import resolve_judgment
from app.evaluation.metrics import CaseScore, score_case
from app.evaluation.report import aggregate_scores, attach_categories
from app.models import Document
from app.providers.base import EmbeddingProvider, RerankerProvider
from app.services import lexical, retrieval
from app.services.vector_store import VectorStoreService

EVAL_LIMIT = 5


@dataclass(frozen=True)
class ModeResult:
    mode: str
    scores: list[CaseScore]
    summary: dict[str, Any]


async def run_dataset(
    session: AsyncSession,
    dataset: EvalDataset,
    documents: dict[str, dict[str, Document]],
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    *,
    mode: str = "all",
    reranker: RerankerProvider | None = None,
) -> dict[str, ModeResult]:
    results: dict[str, ModeResult] = {}
    for retrieval_mode in ("semantic", "lexical"):
        if mode not in ("all", retrieval_mode):
            continue
        cases = dataset.cases_for(mode=retrieval_mode)
        scores: list[CaseScore] = []
        categories: dict[str, str] = {}
        for case in cases:
            scores.append(
                await _run_case(
                    session,
                    case,
                    documents=documents,
                    embeddings=embeddings,
                    vector_store=vector_store,
                    reranker=reranker,
                )
            )
            categories[case.id] = case.category
        summary = aggregate_scores(scores)
        attach_categories(summary, scores, categories)
        summary["cases"] = [score.as_dict() for score in scores]
        results[retrieval_mode] = ModeResult(mode=retrieval_mode, scores=scores, summary=summary)
    return results


async def _run_case(
    session: AsyncSession,
    case: EvalCase,
    *,
    documents: dict[str, dict[str, Document]],
    embeddings: EmbeddingProvider,
    vector_store: VectorStoreService,
    reranker: RerankerProvider | None,
) -> CaseScore:
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

    if case.retrieval_mode == "lexical":
        hits = await lexical.search(
            session, tenant_id=tenant_id, query=case.query, limit=EVAL_LIMIT
        )
        retrieved = [hit.source_id for hit in hits]
        hit_ids = [hit.document_id for hit in hits]
    else:
        hits = await retrieval.search(
            session,
            embeddings,
            vector_store,
            tenant_id=tenant_id,
            query=case.query,
            limit=EVAL_LIMIT,
            reranker=reranker,
        )
        retrieved = [hit.source_id for hit in hits]
        hit_ids = [hit.document_id for hit in hits]

    owned_ids = {document.id for document in tenant_docs.values()}
    leaked = [
        source
        for source, document_id in zip(retrieved, hit_ids, strict=True)
        if document_id not in owned_ids
    ]
    return score_case(case_id=case.id, relevant=relevant, retrieved=retrieved, leaked=leaked)
