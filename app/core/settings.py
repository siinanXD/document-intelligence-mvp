"""Central typed settings.

Every value comes from the environment. Secrets are never hard-coded and never
committed; `.env.example` documents variable names only.
"""

from functools import lru_cache
from typing import Literal

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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
