"""Deterministic generation-quality checks.

These metrics do not call a model. They score an `AskResult` against gold
labels: resolvable citations, citation precision, foreign document ids,
no-evidence behaviour, conflict flags, expected facts and forbidden claims.

Retrieval failure (expected evidence was not supplied to the model) is
recorded separately from generation failure (the model was given enough
evidence and still answered badly). The LLM-judge is not used here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.evaluation.cases import GenerationCase
from app.evaluation.generation import generation_eval_record
from app.evaluation.metrics import mean
from app.evaluation.report import round4
from app.services.qa import AskResult


@dataclass(frozen=True)
class GenerationCaseScore:
    case_id: str
    category: str
    answerable: bool
    expected_conflict: bool
    retrieval_failed: bool
    generation_failed: bool
    has_sufficient_evidence: bool
    conflicting: bool
    cited_source_ids: list[str]
    model_source_ids: list[str]
    dropped_invented_count: int
    relevant_count: int
    relevant_retrieved_count: int
    citation_precision: float | None
    all_citations_resolvable: bool
    foreign_source_ids: list[str]
    forbidden_document_citations: list[str]
    expected_facts_found: list[str]
    expected_facts_missing: list[str]
    forbidden_claim_hits: list[str]
    no_evidence_correct: bool | None
    conflict_correct: bool | None
    factual_with_resolvable_citations: bool | None
    prompt_name: str | None
    prompt_version: str | None
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: float | None
    estimated_cost_usd: float | None
    finish_reason: str | None
    trace_id: str | None
    judge_groundedness: float | None = None
    judge_completeness: float | None = None
    judge_error: str | None = None
    judge_estimated_cost_usd: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "answerable": self.answerable,
            "expected_conflict": self.expected_conflict,
            "retrieval_failed": self.retrieval_failed,
            "generation_failed": self.generation_failed,
            "has_sufficient_evidence": self.has_sufficient_evidence,
            "conflicting": self.conflicting,
            "cited_source_ids": list(self.cited_source_ids),
            "model_source_ids": list(self.model_source_ids),
            "dropped_invented_count": self.dropped_invented_count,
            "relevant_count": self.relevant_count,
            "relevant_retrieved_count": self.relevant_retrieved_count,
            "citation_precision": round4(self.citation_precision),
            "all_citations_resolvable": self.all_citations_resolvable,
            "foreign_source_ids": list(self.foreign_source_ids),
            "forbidden_document_citations": list(self.forbidden_document_citations),
            "expected_facts_found": list(self.expected_facts_found),
            "expected_facts_missing": list(self.expected_facts_missing),
            "forbidden_claim_hits": list(self.forbidden_claim_hits),
            "no_evidence_correct": self.no_evidence_correct,
            "conflict_correct": self.conflict_correct,
            "factual_with_resolvable_citations": self.factual_with_resolvable_citations,
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            "finish_reason": self.finish_reason,
            "trace_id": self.trace_id,
            "judge_groundedness": round4(self.judge_groundedness),
            "judge_completeness": round4(self.judge_completeness),
            "judge_error": self.judge_error,
            "judge_estimated_cost_usd": self.judge_estimated_cost_usd,
        }


@dataclass
class GenerationSummary:
    total_cases: int
    retrieval_failures: int
    generation_failures: int
    retrieval_failed_case_ids: list[str] = field(default_factory=list)
    generation_failed_case_ids: list[str] = field(default_factory=list)
    cross_tenant_leakage: int = 0
    foreign_source_ids: int = 0
    unresolvable_citations: int = 0
    factual_answers: int = 0
    factual_answers_with_resolvable_citations: int = 0
    factual_citation_rate: float | None = None
    citation_precision: float | None = None
    unanswerable_cases: int = 0
    unanswerable_correct: int = 0
    unanswerable_correct_rate: float | None = None
    conflict_scored_cases: int = 0
    conflict_correct: int = 0
    conflict_correct_rate: float | None = None
    forbidden_claim_cases: int = 0
    latency_ms_mean: float | None = None
    input_tokens_sum: int | None = None
    output_tokens_sum: int | None = None
    estimated_cost_usd_sum: float | None = None
    by_category: dict[str, Any] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "retrieval_failures": self.retrieval_failures,
            "generation_failures": self.generation_failures,
            "retrieval_failed_case_ids": list(self.retrieval_failed_case_ids),
            "generation_failed_case_ids": list(self.generation_failed_case_ids),
            "cross_tenant_leakage": self.cross_tenant_leakage,
            "foreign_source_ids": self.foreign_source_ids,
            "unresolvable_citations": self.unresolvable_citations,
            "factual_answers": self.factual_answers,
            "factual_answers_with_resolvable_citations": (
                self.factual_answers_with_resolvable_citations
            ),
            "factual_citation_rate": round4(self.factual_citation_rate),
            "citation_precision": round4(self.citation_precision),
            "unanswerable_cases": self.unanswerable_cases,
            "unanswerable_correct": self.unanswerable_correct,
            "unanswerable_correct_rate": round4(self.unanswerable_correct_rate),
            "conflict_scored_cases": self.conflict_scored_cases,
            "conflict_correct": self.conflict_correct,
            "conflict_correct_rate": round4(self.conflict_correct_rate),
            "forbidden_claim_cases": self.forbidden_claim_cases,
            "latency_ms_mean": round4(self.latency_ms_mean),
            "input_tokens_sum": self.input_tokens_sum,
            "output_tokens_sum": self.output_tokens_sum,
            "estimated_cost_usd_sum": self.estimated_cost_usd_sum,
            "by_category": self.by_category,
            "cases": list(self.cases),
        }


def score_generation_case(
    *,
    case: GenerationCase,
    result: AskResult,
    relevant_ids: set[str],
    retrieved_ids: set[str],
    resolvable_ids: set[str],
    owned_document_ids: set[str],
    cited_document_ids: dict[str, str],
    cited_filenames: dict[str, str],
    judge_groundedness: float | None = None,
    judge_completeness: float | None = None,
    judge_error: str | None = None,
    judge_estimated_cost_usd: float | None = None,
) -> GenerationCaseScore:
    """Score one `/ask` result against gold labels. Does not log the answer."""
    record = generation_eval_record(case_id=case.id, result=result)
    cited = list(record["cited_source_ids"])
    model_ids = list(record["model_source_ids"])
    dropped = max(0, len(model_ids) - len(cited))
    relevant_retrieved = relevant_ids & retrieved_ids
    if case.answerable:
        retrieval_failed = bool(relevant_ids) and relevant_ids - retrieved_ids != set()
    else:
        retrieval_failed = False

    forbidden_hits = [claim for claim in case.forbidden_claims if claim in result.answer]
    facts_found = [fact for fact in case.expected_facts if fact in result.answer]
    facts_missing = [fact for fact in case.expected_facts if fact not in result.answer]
    foreign = [
        source_id
        for source_id in cited
        if cited_document_ids.get(source_id) not in owned_document_ids
    ]
    forbidden_docs = [
        cited_filenames[source_id]
        for source_id in cited
        if cited_filenames.get(source_id) in case.forbidden_documents
    ]
    all_resolvable = all(source_id in resolvable_ids for source_id in cited)
    precision = None
    if cited:
        precision = len(set(cited) & relevant_ids) / len(set(cited)) if relevant_ids else 0.0

    no_evidence_correct = None
    if not case.answerable:
        no_evidence_correct = result.has_sufficient_evidence is False

    conflict_correct = None
    if case.expected_conflict and not retrieval_failed:
        conflict_correct = result.conflicting is True

    factual = result.has_sufficient_evidence is True
    factual_ok = None
    if factual:
        factual_ok = bool(cited) and all_resolvable and not foreign

    generation_failed = _generation_failed(
        case=case,
        retrieval_failed=retrieval_failed,
        result=result,
        all_resolvable=all_resolvable,
        foreign=foreign,
        forbidden_docs=forbidden_docs,
        facts_missing=facts_missing,
        forbidden_hits=forbidden_hits,
        no_evidence_correct=no_evidence_correct,
        conflict_correct=conflict_correct,
        factual_ok=factual_ok,
    )
    return GenerationCaseScore(
        case_id=case.id,
        category=case.category,
        answerable=case.answerable,
        expected_conflict=case.expected_conflict,
        retrieval_failed=retrieval_failed,
        generation_failed=generation_failed,
        has_sufficient_evidence=result.has_sufficient_evidence,
        conflicting=result.conflicting,
        cited_source_ids=cited,
        model_source_ids=model_ids,
        dropped_invented_count=dropped,
        relevant_count=len(relevant_ids),
        relevant_retrieved_count=len(relevant_retrieved),
        citation_precision=precision,
        all_citations_resolvable=all_resolvable,
        foreign_source_ids=foreign,
        forbidden_document_citations=forbidden_docs,
        expected_facts_found=facts_found,
        expected_facts_missing=facts_missing,
        forbidden_claim_hits=forbidden_hits,
        no_evidence_correct=no_evidence_correct,
        conflict_correct=conflict_correct,
        factual_with_resolvable_citations=factual_ok,
        prompt_name=record["prompt_name"],
        prompt_version=record["prompt_version"],
        provider=record["provider"],
        model=record["model"],
        input_tokens=record["input_tokens"],
        output_tokens=record["output_tokens"],
        total_tokens=record["total_tokens"],
        latency_ms=record["latency_ms"],
        estimated_cost_usd=record["estimated_cost_usd"],
        finish_reason=record["finish_reason"],
        trace_id=record["trace_id"],
        judge_groundedness=judge_groundedness,
        judge_completeness=judge_completeness,
        judge_error=judge_error,
        judge_estimated_cost_usd=judge_estimated_cost_usd,
    )


def _generation_failed(
    *,
    case: GenerationCase,
    retrieval_failed: bool,
    result: AskResult,
    all_resolvable: bool,
    foreign: list[str],
    forbidden_docs: list[str],
    facts_missing: list[str],
    forbidden_hits: list[str],
    no_evidence_correct: bool | None,
    conflict_correct: bool | None,
    factual_ok: bool | None,
) -> bool:
    if foreign or not all_resolvable or forbidden_hits or forbidden_docs:
        return True
    if factual_ok is False:
        return True
    if not case.answerable:
        return no_evidence_correct is False
    if retrieval_failed:
        # The model was not given the gold evidence. A no-evidence answer is
        # correct here; a confident ungrounded answer is a generation failure.
        return result.has_sufficient_evidence is True
    if facts_missing:
        return True
    return case.expected_conflict and conflict_correct is False


def aggregate_generation_scores(scores: list[GenerationCaseScore]) -> GenerationSummary:
    retrieval_failed = [score for score in scores if score.retrieval_failed]
    generation_failed = [score for score in scores if score.generation_failed]
    foreign = [source for score in scores for source in score.foreign_source_ids]
    unresolvable = [
        score.case_id
        for score in scores
        if score.cited_source_ids and not score.all_citations_resolvable
    ]
    factual = [score for score in scores if score.has_sufficient_evidence]
    factual_ok = [score for score in factual if score.factual_with_resolvable_citations]
    precisable = [
        score.citation_precision
        for score in scores
        if score.citation_precision is not None and not score.retrieval_failed
    ]
    unanswerable = [score for score in scores if not score.answerable]
    unanswerable_ok = [score for score in unanswerable if score.no_evidence_correct]
    conflict = [score for score in scores if score.expected_conflict and not score.retrieval_failed]
    conflict_ok = [score for score in conflict if score.conflict_correct]
    forbidden_cases = [score for score in scores if score.forbidden_claim_hits]
    latencies = [score.latency_ms for score in scores if score.latency_ms is not None]
    input_tokens = [score.input_tokens for score in scores if score.input_tokens is not None]
    output_tokens = [score.output_tokens for score in scores if score.output_tokens is not None]
    costs = [
        (score.estimated_cost_usd or 0.0) + (score.judge_estimated_cost_usd or 0.0)
        for score in scores
        if score.estimated_cost_usd is not None or score.judge_estimated_cost_usd is not None
    ]
    grouped: dict[str, list[GenerationCaseScore]] = {}
    for score in scores:
        grouped.setdefault(score.category, []).append(score)

    by_category = {
        name: {
            "total_cases": len(group),
            "retrieval_failures": sum(1 for item in group if item.retrieval_failed),
            "generation_failures": sum(1 for item in group if item.generation_failed),
            "unanswerable_correct_rate": round4(
                mean(
                    [
                        1.0 if item.no_evidence_correct else 0.0
                        for item in group
                        if item.no_evidence_correct is not None
                    ]
                )
                if any(item.no_evidence_correct is not None for item in group)
                else None
            ),
            "conflict_correct_rate": round4(
                mean(
                    [
                        1.0 if item.conflict_correct else 0.0
                        for item in group
                        if item.conflict_correct is not None
                    ]
                )
                if any(item.conflict_correct is not None for item in group)
                else None
            ),
        }
        for name, group in sorted(grouped.items())
    }
    return GenerationSummary(
        total_cases=len(scores),
        retrieval_failures=len(retrieval_failed),
        generation_failures=len(generation_failed),
        retrieval_failed_case_ids=sorted(score.case_id for score in retrieval_failed),
        generation_failed_case_ids=sorted(score.case_id for score in generation_failed),
        cross_tenant_leakage=len(foreign),
        foreign_source_ids=len(foreign),
        unresolvable_citations=len(unresolvable),
        factual_answers=len(factual),
        factual_answers_with_resolvable_citations=len(factual_ok),
        factual_citation_rate=(len(factual_ok) / len(factual)) if factual else None,
        citation_precision=mean(precisable) if precisable else None,
        unanswerable_cases=len(unanswerable),
        unanswerable_correct=len(unanswerable_ok),
        unanswerable_correct_rate=(
            (len(unanswerable_ok) / len(unanswerable)) if unanswerable else None
        ),
        conflict_scored_cases=len(conflict),
        conflict_correct=len(conflict_ok),
        conflict_correct_rate=(len(conflict_ok) / len(conflict)) if conflict else None,
        forbidden_claim_cases=len(forbidden_cases),
        latency_ms_mean=mean(latencies) if latencies else None,
        input_tokens_sum=sum(input_tokens) if input_tokens else None,
        output_tokens_sum=sum(output_tokens) if output_tokens else None,
        estimated_cost_usd_sum=round(sum(costs), 8) if costs else None,
        by_category=by_category,
        cases=[score.as_dict() for score in scores],
    )
