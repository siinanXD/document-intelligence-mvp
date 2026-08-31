"""Provider selection.

Call sites ask for a capability; settings decide the implementation. No call
site names a vendor.
"""

from functools import lru_cache

from app.core.settings import get_settings
from app.providers.base import EmbeddingProvider, LLMProvider
from app.providers.local_storage import LocalStorageBackend
from app.providers.openai_provider import (
    OpenAIEmbeddingProvider,
    OpenAILLMProvider,
    build_openai_client,
)
from app.providers.parsing import DocumentParser
from app.providers.s3_storage import S3StorageBackend, build_s3_client
from app.providers.storage import StorageBackend


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
            batch_size=settings.embedding_batch_size,
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
            client=build_openai_client(
                settings.openai_api_key,
                timeout_seconds=settings.llm_timeout_seconds,
                max_retries=0,
            ),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
            input_usd_per_million=settings.llm_input_usd_per_million,
            output_usd_per_million=settings.llm_output_usd_per_million,
        )
    raise ProviderConfigurationError(f"unsupported LLM provider: {settings.llm_provider}")


@lru_cache
def get_storage_backend() -> StorageBackend:
    settings = get_settings()

    if settings.storage_backend == "local":
        return LocalStorageBackend(settings.storage_local_path)

    if settings.storage_backend == "s3":
        missing = [
            name
            for name, value in (
                ("S3_BUCKET", settings.s3_bucket),
                ("S3_ACCESS_KEY_ID", settings.s3_access_key_id),
                ("S3_SECRET_ACCESS_KEY", settings.s3_secret_access_key),
            )
            if not value
        ]
        if missing:
            raise ProviderConfigurationError(f"missing S3 configuration: {', '.join(missing)}")
        return S3StorageBackend(
            client=build_s3_client(
                endpoint_url=settings.s3_endpoint_url,
                region=settings.s3_region,
                access_key_id=settings.s3_access_key_id,
                secret_access_key=settings.s3_secret_access_key,
            ),
            bucket=settings.s3_bucket,
        )

    raise ProviderConfigurationError(f"unsupported storage backend: {settings.storage_backend}")


@lru_cache
def get_document_parser() -> DocumentParser:
    """Build the configured parser.

    Docling is imported here rather than at module scope: it drags in a very
    large dependency tree, and nothing else in the application needs it.
    """
    from app.providers.docling_parser import DoclingParser

    settings = get_settings()
    return DoclingParser(
        artifacts_path=settings.docling_artifacts_path,
        do_ocr=settings.docling_do_ocr,
        do_table_structure=settings.docling_do_table_structure,
    )
