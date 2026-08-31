"""Fields SIN-74 will record per evaluated generation.

This is not a generation-quality evaluator. It projects an `AskResult` into a
stable dict so SIN-74 can persist prompt, model, tokens, latency, cost, cited
source ids, finish status and trace id without rewriting the provider layer.
"""

from __future__ import annotations

from typing import Any

from app.services.qa import AskResult


def generation_eval_record(*, case_id: str, result: AskResult) -> dict[str, Any]:
    generation = result.generation
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
        "finish_reason": None if generation is None else generation.finish_reason,
        "trace_id": None if generation is None else generation.trace_id,
        "request_id": None if generation is None else generation.request_id,
        "has_sufficient_evidence": result.has_sufficient_evidence,
        "conflicting": result.conflicting,
    }
