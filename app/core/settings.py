"""Central typed settings.

Every value comes from the environment. Secrets are never hard-coded and never
committed; `.env.example` documents variable names only.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "document_chunks"
    # Whole seconds: the Qdrant client takes an int, and a truncated
    # sub-second value would read as "no timeout".
    qdrant_timeout_seconds: int = Field(default=5, ge=1)

    # --- AI providers ---
    embedding_provider: Literal["openai"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_version: str = "v1"
    llm_provider: Literal["openai"] = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None

    # --- Object storage ---
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./storage"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # --- Uploads ---
    # 50 MiB. Enforced while streaming, so an oversized body is refused before
    # it is buffered rather than after.
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)

    # --- Health ---
    health_check_timeout_seconds: float = Field(default=2.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
