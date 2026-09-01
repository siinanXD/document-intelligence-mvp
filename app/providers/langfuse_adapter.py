"""Langfuse adapter. Imported only when tracing_provider=langfuse.

The Langfuse SDK is optional and is never imported by services, routes or the
OpenAI provider. Construction and every record call fail open: missing package,
missing credentials, or a SDK error become a no-op after a type-only log.
"""

from __future__ import annotations

import logging
from typing import Any

from app.providers.generation import GenerationResult, RetrievalTrace
from app.providers.tracing import TracingAdapter

logger = logging.getLogger(__name__)


class LangfuseTracingAdapter(TracingAdapter):
    def __init__(
        self,
        client: Any,
        *,
        capture_content: bool = False,
        propagate: Any | None = None,
    ) -> None:
        self._client = client
        self._capture_content = capture_content
        # `langfuse.propagate_attributes`, injected so unit tests can run
        # without the SDK installed. When absent, correlating attributes are
        # still recorded in observation metadata and the propagation is skipped.
        self._propagate = propagate

    @classmethod
    def from_settings(cls, settings) -> LangfuseTracingAdapter | None:
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            logger.warning(
                "langfuse tracing requested without credentials",
                extra={"error_type": "MissingCredentials"},
            )
            return None
        try:
            from langfuse import Langfuse, propagate_attributes
        except ImportError:
            logger.warning(
                "langfuse package is not installed",
                extra={"error_type": "ImportError"},
            )
            return None

        # v4 renamed the server URL to `base_url`. `environment` replaces the
        # trace-level `release`/`environment` fields removed from the SDK, so
        # every observation is tagged with the deployment environment.
        host = settings.langfuse_host or settings.langfuse_base_url
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=host,
            environment=settings.environment,
        )
        return cls(
            client,
            capture_content=settings.tracing_capture_content,
            propagate=propagate_attributes,
        )

    @property
    def captures_content(self) -> bool:
        return self._capture_content

    async def record_generation(
        self,
        result: GenerationResult[Any],
        *,
        extra: dict[str, object] | None = None,
        input_payload: object | None = None,
        output_payload: object | None = None,
    ) -> None:
        metadata = result.safe_metadata()
        if extra:
            metadata.update(extra)
        usage_details: dict[str, int] = {}
        if result.input_tokens is not None:
            usage_details["input"] = result.input_tokens
        if result.output_tokens is not None:
            usage_details["output"] = result.output_tokens
        if result.total_tokens is not None:
            usage_details["total"] = result.total_tokens
        model_parameters = {
            "prompt_name": result.prompt_name,
            "prompt_version": result.prompt_version,
        }
        kwargs: dict[str, object] = {
            "as_type": "generation",
            "name": result.prompt_name,
            "model": result.model,
            "metadata": metadata,
            "model_parameters": model_parameters,
        }
        if usage_details:
            kwargs["usage_details"] = usage_details
        if result.estimated_cost_usd is not None:
            kwargs["cost_details"] = {"total": result.estimated_cost_usd}
        if self._capture_content:
            if input_payload is not None:
                kwargs["input"] = input_payload
            if output_payload is not None:
                kwargs["output"] = _jsonable(output_payload)
        # v4: propagate correlating attributes onto the (cost-bearing) root
        # observation. trace_id is the request id, else the job id, else a
        # fresh uuid, so a request's generation and retrieval share a session.
        extra_map = extra or {}
        self._observe(
            session_id=result.trace_id,
            user_id=extra_map.get("tenant_id"),
            **kwargs,
        )

    async def record_retrieval(
        self,
        retrieval: RetrievalTrace,
        *,
        extra: dict[str, object] | None = None,
    ) -> None:
        metadata = retrieval.safe_metadata()
        extra_map = extra or {}
        if extra_map:
            metadata.update(extra_map)
        self._observe(
            as_type="span",
            name="retrieval",
            metadata=metadata,
            session_id=extra_map.get("request_id") or extra_map.get("job_id"),
            user_id=extra_map.get("tenant_id"),
        )

    def _observe(
        self,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        **kwargs: object,
    ) -> None:
        attributes = {}
        if session_id:
            attributes["session_id"] = session_id
        if user_id:
            attributes["user_id"] = user_id
        if self._propagate is not None and attributes:
            with self._propagate(**attributes):
                self._start_observation(**kwargs)
        else:
            self._start_observation(**kwargs)

    def _start_observation(self, **kwargs: object) -> None:
        observation = self._client.start_as_current_observation(**kwargs)
        if hasattr(observation, "__enter__"):
            with observation:
                pass


def _jsonable(value: object) -> object:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    return value
