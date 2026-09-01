"""Project an `AskResult` into privacy-safe evaluation fields.

Persists prompt identity, provider/model, tokens, latency, cost, cited
source ids, finish status and trace id. Never stores the question, the
answer, or passage text.
"""

from __future__ import annotations

from typing import Any

from app.services.qa import AskResult, GroundedAnswer


def generation_eval_record(*, case_id: str, result: AskResult) -> dict[str, Any]:
    generation = result.generation
    model_source_ids: list[str] = []
    content = None if generation is None else generation.content
    if isinstance(content, GroundedAnswer):
        model_source_ids = list(content.source_ids)
    return {
        "dataset_case_id": case_id,
        "prompt_name": None if generation is None else generation.prompt_name,
        "prompt_version": None if generation is None else generation.prompt_version,
        "provider": None if generation is None else generation.provider,
        "model": None if generation is None else generation.model,
        "input_tokens": None if generation is None else generation.input_tokens,
        "output_tokens": None if generation is None else generation.output_tokens,
        "total_tokens": None if generation is None else generation.total_tokens,
        "latency_ms": None if generation is None else generation.latency_ms,
        "estimated_cost_usd": None if generation is None else generation.estimated_cost_usd,
        "cited_source_ids": [hit.source_id for hit in result.sources],
        "model_source_ids": model_source_ids,
        "finish_reason": None if generation is None else generation.finish_reason,
        "trace_id": None if generation is None else generation.trace_id,
        "request_id": None if generation is None else generation.request_id,
        "has_sufficient_evidence": result.has_sufficient_evidence,
        "conflicting": result.conflicting,
    }
