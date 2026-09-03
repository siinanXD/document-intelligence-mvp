"""Central typed settings.

Every value comes from the environment. Secrets are never hard-coded and never
committed; `.env.example` documents variable names only.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.database_url import async_database_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "document-intelligence-mvp"
    environment: Literal["local", "ci", "staging", "production"] = "local"
    log_level: str = "INFO"

    # --- PostgreSQL ---
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/document_intelligence"
    )
    database_pool_size: int = 5
    database_echo: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def _asyncpg_database_url(cls, value: object) -> object:
        if isinstance(value, str):
            return async_database_url(value)
        return value

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "document_chunks"
    qdrant_documents_collection: str = "documents"
    # Whole seconds: the Qdrant client takes an int, and a truncated
    # sub-second value would read as "no timeout".
    qdrant_timeout_seconds: int = Field(default=5, ge=1)

    # --- AI providers ---
    # Switching the embedding provider or model is a configuration change plus
    # a reindex: stored vectors keep their recorded identity, mismatch is
    # detected, and docs/PROVIDERS.md describes the required rebuild.
    embedding_provider: Literal["openai", "huggingface"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_version: str = "v1"
    embedding_batch_size: int = Field(default=128, ge=1, le=2048)
    llm_provider: Literal["openai"] = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    # --- Hugging Face / TEI-compatible endpoints ---
    # Base URL of a Text Embeddings Inference-compatible server. Where it runs
    # (local container, GPU host outside Railway, hosted inference) is a
    # deployment concern; the application only ever sees this URL.
    huggingface_embeddings_base_url: str | None = None
    # Separate from EMBEDDING_MODEL so selecting Hugging Face cannot silently
    # inherit the OpenAI default as its persisted model identity.
    huggingface_embedding_model: str | None = None
    huggingface_api_key: str | None = None
    # Required with the huggingface provider: the endpoint serves one model
    # whose width the application must not guess. Verified against every
    # response so a wrong value fails before any vector write.
    huggingface_embedding_dimensions: int | None = Field(default=None, ge=1)
    huggingface_timeout_seconds: float = Field(default=30.0, gt=0)
    # TEI endpoints reject oversized batches; keep the default conservative.
    huggingface_embedding_batch_size: int = Field(default=32, ge=1, le=2048)

    # --- Reranker (optional second retrieval stage; off by default) ---
    reranker_provider: Literal["none", "huggingface"] = "none"
    # Identity marker only: a TEI rerank endpoint serves one fixed model.
    reranker_model: str = ""
    huggingface_rerank_base_url: str | None = None
    # When reranking, first-stage retrieval fetches limit * multiplier
    # candidates (capped) so the reranker has something to reorder.
    reranker_candidate_multiplier: int = Field(default=4, ge=1, le=20)
    reranker_max_candidates: int = Field(default=50, ge=1)
    # Generation controls. These are explicit so a vendor SDK default cannot
    # silently change timeout, retries or sampling under us.
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    # Additional attempts after the first. The OpenAI client is constructed
    # with max_retries=0 so application retries do not stack on SDK retries.
    llm_max_retries: int = Field(default=2, ge=0)
    llm_max_output_tokens: int = Field(default=1024, ge=1)
    # 0.0 is intentional: grounded answers and profiles must be reproducible.
    # top_p, seed and frequency/presence penalties are not sent; gpt-4o-mini
    # accepts temperature, and we do not pretend to set unsupported knobs.
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    # USD per 1 million tokens. Unset means estimated_cost_usd stays None,
    # never 0.0. Prices are configured, not guessed from a vendor price list.
    llm_input_usd_per_million: float | None = Field(default=None, ge=0.0)
    llm_output_usd_per_million: float | None = Field(default=None, ge=0.0)

    # --- Observability ---
    # Requests must work with tracing off. Langfuse is optional.
    tracing_provider: Literal["none", "langfuse"] = "none"
    # Development-only: include prompts and model output in traces. Off by
    # default. Unrelated debug flags must never turn this on.
    tracing_capture_content: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    langfuse_base_url: str | None = None

    # --- Object storage ---
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # --- Parsing ---
    # Pre-fetched Docling model weights. Left unset, Docling downloads them on
    # first use - hundreds of megabytes, inside whichever request happens to be
    # the first PDF. Deployments bake the models in and point this at them.
    docling_artifacts_path: str | None = None
    # OCR is the expensive path and pulls further models; scanned documents are
    # out of scope for the MVP.
    docling_do_ocr: bool = False
    docling_do_table_structure: bool = True

    # --- Worker ---
    worker_id: str = "worker"
    worker_batch_size: int = Field(default=1, ge=1, le=32)
    worker_idle_sleep_seconds: float = Field(default=2.0, gt=0)
    worker_retry_delay_seconds: int = Field(default=60, ge=1)

    # --- Search ---
    search_default_limit: int = Field(default=10, ge=1)
    search_max_limit: int = Field(default=50, ge=1)
    # Fewer passages than search returns: every one of them costs prompt
    # tokens, and a long tail of weak matches makes grounding worse, not better.
    ask_default_limit: int = Field(default=6, ge=1)
    ask_max_limit: int = Field(default=20, ge=1)

    # --- Document relations ---
    # Thresholds live here rather than in the detection code, so tuning them is
    # a configuration change and every one of them is visible in one place.
    relation_version_similarity: float = Field(default=0.92, ge=0.0, le=1.0)
    relation_related_similarity: float = Field(default=0.75, ge=0.0, le=1.0)
    relation_min_shared_identifiers: int = Field(default=1, ge=1)
    relation_min_shared_entities: int = Field(default=2, ge=1)
    relation_max_candidates: int = Field(default=10, ge=1, le=100)

    # --- Uploads ---
    # 50 MiB. Enforced while streaming, so an oversized body is refused before
    # it is buffered rather than after.
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)

    # --- Health ---
    health_check_timeout_seconds: float = Field(default=2.0, gt=0)

    # --- Browser cockpit (SIN-77) ---
    # Comma-separated origins allowed to call the API from a browser.
    # Empty means local defaults when ENVIRONMENT=local, and no CORS otherwise.
    cors_origins: str = ""

    def cors_origin_list(self) -> list[str]:
        explicit = [part.strip() for part in self.cors_origins.split(",") if part.strip()]
        if explicit:
            return explicit
        if self.environment == "local":
            return ["http://127.0.0.1:3000", "http://localhost:3000"]
        return []


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
