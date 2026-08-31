"""Provider-neutral tracing for generations and retrieval.

Vendor SDKs stay behind an adapter. The default is a no-op, so requests work
without tracing configured. Adapter failures are swallowed after a type-only
log line: observability must never fail a customer request.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

from app.core.settings import get_settings
from app.providers.generation import GenerationResult, RetrievalTrace

logger = logging.getLogger(__name__)


class TracingAdapter(ABC):
    """Records generation and retrieval metadata. Implementations must fail open."""

    @property
    @abstractmethod
    def captures_content(self) -> bool:
        """True only when development-sensitive prompt/output capture is on."""

    @abstractmethod
    async def record_generation(
        self,
        result: GenerationResult[Any],
        *,
        extra: dict[str, object] | None = None,
        input_payload: object | None = None,
        output_payload: object | None = None,
    ) -> None:
        """Record one completed generation. Must not raise to the caller."""

    @abstractmethod
    async def record_retrieval(
        self,
        retrieval: RetrievalTrace,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Record retrieval metadata without chunk text. Must not raise."""


class NullTracingAdapter(TracingAdapter):
    """Clean no-op used when tracing is disabled or misconfigured."""

    @property
    def captures_content(self) -> bool:
        return False

    async def record_generation(
        self,
        result: GenerationResult[Any],
        *,
        extra: dict[str, object] | None = None,
        input_payload: object | None = None,
        output_payload: object | None = None,
    ) -> None:
        return None

    async def record_retrieval(
        self,
        retrieval: RetrievalTrace,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        return None


def _safe_log_failure(action: str, exc: BaseException) -> None:
    logger.warning(
        "tracing failed",
        extra={"action": action, "error_type": type(exc).__name__},
    )


class FailOpenTracingAdapter(TracingAdapter):
    """Wraps another adapter so its exceptions never escape."""

    def __init__(self, inner: TracingAdapter) -> None:
        self._inner = inner

    @property
    def captures_content(self) -> bool:
        return self._inner.captures_content

    async def record_generation(
        self,
        result: GenerationResult[Any],
        *,
        extra: dict[str, object] | None = None,
        input_payload: object | None = None,
        output_payload: object | None = None,
    ) -> None:
        try:
            await self._inner.record_generation(
                result,
                extra=extra,
                input_payload=input_payload,
                output_payload=output_payload,
            )
        except Exception as exc:
            _safe_log_failure("record_generation", exc)

    async def record_retrieval(
        self,
        retrieval: RetrievalTrace,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        try:
            await self._inner.record_retrieval(retrieval, extra=extra)
        except Exception as exc:
            _safe_log_failure("record_retrieval", exc)


def build_tracing_adapter(settings) -> TracingAdapter:
    """Construct the adapter for `settings`. Never raises."""
    if settings.tracing_provider == "none":
        return NullTracingAdapter()
    if settings.tracing_provider == "langfuse":
        try:
            from app.providers.langfuse_adapter import LangfuseTracingAdapter

            adapter = LangfuseTracingAdapter.from_settings(settings)
        except Exception as exc:
            _safe_log_failure("build_langfuse", exc)
            return NullTracingAdapter()
        if adapter is None:
            return NullTracingAdapter()
        return FailOpenTracingAdapter(adapter)
    logger.warning(
        "unknown tracing provider",
        extra={"error_type": "ProviderConfigurationError"},
    )
    return NullTracingAdapter()


@lru_cache
def get_tracing_adapter() -> TracingAdapter:
    return build_tracing_adapter(get_settings())
