"""OpenAI implementations of the provider interfaces.

The OpenAI SDK is imported here and nowhere else. Both classes accept an
injected client so tests can supply a fake and never make a network call.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from app.core.correlation import correlation_extra, current_request_id, current_trace_id
from app.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    ProviderResponseError,
    SchemaT,
)
from app.providers.generation import (
    GenerationResult,
    derive_total_tokens,
    estimate_cost_usd,
)
from app.providers.prompts import Prompt
from app.providers.tracing import TracingAdapter, get_tracing_adapter

logger = logging.getLogger(__name__)

# Re-exported for call sites that import it from here.
__all__ = [
    "OpenAIEmbeddingProvider",
    "OpenAILLMProvider",
    "ProviderResponseError",
    "build_openai_client",
]

# Dimensionality of the models we support. Unknown models must be declared
# explicitly rather than guessed, so an unsupported one fails loudly.
_MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

# Transient failures the application may retry. SDK retries are disabled on the
# client (max_retries=0) so this is the only retry loop.
_TRANSIENT_ERRORS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        client: Any,
        model: str = "text-embedding-3-small",
        version: str = "v1",
        batch_size: int = 128,
    ) -> None:
        if model not in _MODEL_DIMENSIONS:
            raise ValueError(f"unknown embedding model: {model}")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._client = client
        self._model = model
        self._version = version
        self._batch_size = batch_size

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return _MODEL_DIMENSIONS[self._model]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, preserving input order.

        A long document produces more chunks than one request should carry, so
        the input is split. Batches run in sequence rather than concurrently:
        the provider rate-limits per minute, and a burst of parallel requests
        buys latency at the cost of being throttled.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.embeddings.create(model=self._model, input=batch)
            # The API preserves input order, but sorting by index makes that
            # explicit rather than assumed.
            items = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in items)

        if len(vectors) != len(texts):
            raise ProviderResponseError(
                f"{self._model} returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors


class OpenAILLMProvider(LLMProvider):
    def __init__(
        self,
        client: Any,
        model: str = "gpt-4o-mini",
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        max_output_tokens: int = 1024,
        temperature: float = 0.0,
        input_usd_per_million: float | None = None,
        output_usd_per_million: float | None = None,
        tracer: TracingAdapter | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if max_retries < 0:
            raise ValueError("max_retries must be at least 0")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._input_usd_per_million = input_usd_per_million
        self._output_usd_per_million = output_usd_per_million
        self._tracer = tracer

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def _call_kwargs(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
            "timeout": self._timeout_seconds,
        }

    def _tracer_or_default(self) -> TracingAdapter:
        return self._tracer if self._tracer is not None else get_tracing_adapter()

    async def complete(self, prompt: Prompt, user: str) -> GenerationResult[str]:
        messages = [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user},
        ]

        async def _invoke():
            return await self._client.chat.completions.create(
                messages=messages, **self._call_kwargs()
            )

        response, retry_count, latency_ms = await self._call_with_retries(_invoke)
        content = response.choices[0].message.content or ""
        result = self._envelope(content, prompt, response, retry_count, latency_ms)
        await self._emit_generation(result, messages=messages, output=content)
        return result

    async def complete_structured(
        self, prompt: Prompt, user: str, schema: type[SchemaT]
    ) -> GenerationResult[SchemaT]:
        messages = [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user},
        ]

        async def _invoke():
            return await self._client.chat.completions.parse(
                messages=messages,
                response_format=schema,
                **self._call_kwargs(),
            )

        response, retry_count, latency_ms = await self._call_with_retries(_invoke)
        parsed = response.choices[0].message.parsed
        if parsed is None:
            # A refusal or an unparseable response arrives as parsed=None.
            # Returning it would defer the failure to a far-away call site.
            # This is deterministic: do not retry it.
            raise ProviderResponseError(f"{self._model} returned no parsable {schema.__name__}")
        result = self._envelope(parsed, prompt, response, retry_count, latency_ms)
        await self._emit_generation(result, messages=messages, output=parsed)
        return result

    async def _call_with_retries(self, invoke):
        attempts = 0
        started = time.perf_counter()
        while True:
            try:
                response = await invoke()
                latency_ms = (time.perf_counter() - started) * 1000
                return response, attempts, latency_ms
            except Exception as exc:
                if not _is_transient(exc) or attempts >= self._max_retries:
                    raise
                attempts += 1
                logger.warning(
                    "retrying generation after a transient provider error",
                    extra={"error_type": type(exc).__name__, "retry_count": attempts},
                )

    def _envelope(
        self,
        content: Any,
        prompt: Prompt,
        response: Any,
        retry_count: int,
        latency_ms: float,
    ) -> GenerationResult[Any]:
        usage = getattr(response, "usage", None)
        input_tokens = _usage_value(usage, "prompt_tokens")
        output_tokens = _usage_value(usage, "completion_tokens")
        total_tokens = _usage_value(usage, "total_tokens")
        if total_tokens is None:
            total_tokens = derive_total_tokens(input_tokens, output_tokens)
        finish_reason = None
        choices = getattr(response, "choices", None)
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)
        return GenerationResult(
            content=content,
            provider=self.provider,
            model=self._model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            latency_ms=round(latency_ms, 3),
            retry_count=retry_count,
            trace_id=current_trace_id(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimate_cost_usd(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_usd_per_million=self._input_usd_per_million,
                output_usd_per_million=self._output_usd_per_million,
            ),
            finish_reason=finish_reason,
            request_id=current_request_id(),
        )

    async def _emit_generation(
        self, result: GenerationResult[Any], *, messages: list, output: Any
    ) -> None:
        try:
            tracer = self._tracer_or_default()
            capture = tracer.captures_content
            await tracer.record_generation(
                result,
                extra=correlation_extra(),
                input_payload=messages if capture else None,
                output_payload=output if capture else None,
            )
        except Exception as exc:
            logger.warning(
                "generation tracing failed",
                extra={"error_type": type(exc).__name__},
            )


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_ERRORS):
        return True
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        return isinstance(status, int) and status >= 500
    return False


def _usage_value(usage: Any, name: str) -> int | None:
    if usage is None:
        return None
    value = getattr(usage, name, None)
    return value if isinstance(value, int) else None


def build_openai_client(
    api_key: str,
    *,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> AsyncOpenAI:
    """Construct a real OpenAI client. Never called from tests."""
    kwargs: dict[str, Any] = {"api_key": api_key}
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncOpenAI(**kwargs)
