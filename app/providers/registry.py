"""Provider selection.

Call sites ask for a capability; settings decide the implementation. No call
site names a vendor.
"""

from functools import lru_cache

from app.core.settings import get_settings
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.openai_provider import (
    OpenAIEmbeddingProvider,
    OpenAILLMProvider,
    build_openai_client,
)


class ProviderConfigurationError(RuntimeError):
    """Raised when the configured provider cannot be constructed."""


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            raise ProviderConfigurationError("OPENAI_API_KEY is not configured")
        return OpenAIEmbeddingProvider(
            client=build_openai_client(settings.openai_api_key),
            model=settings.embedding_model,
            version=settings.embedding_version,
        )
    raise ProviderConfigurationError(
        f"unsupported embedding provider: {settings.embedding_provider}"
    )


@lru_cache
def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ProviderConfigurationError("OPENAI_API_KEY is not configured")
        return OpenAILLMProvider(
            client=build_openai_client(settings.openai_api_key),
            model=settings.llm_model,
        )
    raise ProviderConfigurationError(f"unsupported LLM provider: {settings.llm_provider}")
