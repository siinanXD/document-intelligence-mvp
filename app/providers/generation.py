"""Provider-neutral generation envelope.

Call sites receive structured content as `content` and observability metadata
beside it. Token counts come from the provider response when it supplies them.
Cost is calculated only when both usage and configured prices are present;
unknown stays `None`, never `0.0`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from app.providers.prompts import Prompt

T = TypeVar("T")


@dataclass(frozen=True)
class GenerationResult(Generic[T]):
    """One completed generation, with metadata independent of the vendor."""

    content: T
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    latency_ms: float
    retry_count: int
    trace_id: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    finish_reason: str | None = None
    request_id: str | None = None

    def safe_metadata(self) -> dict[str, object]:
        """Identifiers and measurements only: never content, prompts or answers."""
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "finish_reason": self.finish_reason,
            "retry_count": self.retry_count,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class RetrievalSourceTrace:
    source_id: str
    document_id: str
    rank: int
    score: float


@dataclass(frozen=True)
class RetrievalTrace:
    """Retrieval quality metadata. Does not carry chunk text."""

    mode: str
    candidate_count: int
    supplied_count: int
    sources: tuple[RetrievalSourceTrace, ...] = field(default_factory=tuple)

    def safe_metadata(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "candidate_count": self.candidate_count,
            "supplied_count": self.supplied_count,
            "sources": [
                {
                    "source_id": source.source_id,
                    "document_id": source.document_id,
                    "rank": source.rank,
                    "score": source.score,
                }
                for source in self.sources
            ],
        }


def estimate_cost_usd(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
) -> float | None:
    """Return a dollar cost only when prices and both token counts are known.

    A configured price of zero is a real number. Missing configuration or
    missing usage is `None`, not `0.0`.
    """
    if input_usd_per_million is None or output_usd_per_million is None:
        return None
    if input_tokens is None or output_tokens is None:
        return None
    return round(
        (input_tokens / 1_000_000) * input_usd_per_million
        + (output_tokens / 1_000_000) * output_usd_per_million,
        8,
    )


def derive_total_tokens(input_tokens: int | None, output_tokens: int | None) -> int | None:
    if input_tokens is None or output_tokens is None:
        return None
    return input_tokens + output_tokens


def generation_from_prompt(
    content: T,
    prompt: Prompt,
    *,
    provider: str,
    model: str,
    latency_ms: float = 0.0,
    retry_count: int = 0,
    trace_id: str = "test-trace",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    estimated_cost_usd: float | None = None,
    finish_reason: str | None = "stop",
    request_id: str | None = None,
) -> GenerationResult[T]:
    """Build an envelope for tests and fakes. Not used on the production path."""
    return GenerationResult(
        content=content,
        provider=provider,
        model=model,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        latency_ms=latency_ms,
        retry_count=retry_count,
        trace_id=trace_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimated_cost_usd,
        finish_reason=finish_reason,
        request_id=request_id,
    )
