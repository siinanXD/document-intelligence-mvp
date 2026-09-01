from app.core.settings import get_settings


def test_settings_default_to_local():
    settings = get_settings()

    assert settings.app_name == "document-intelligence-mvp"
    assert settings.environment == "local"


def test_settings_are_cached():
    assert get_settings() is get_settings()


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    assert get_settings().log_level == "DEBUG"


def test_dependency_settings_read_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/name")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "custom_chunks")

    settings = get_settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/name"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.qdrant_collection == "custom_chunks"


def test_credentials_default_to_unset(monkeypatch):
    """Defaults, not whatever happens to be in a local .env."""
    from app.core.settings import Settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    assert settings.openai_api_key is None
    assert settings.qdrant_api_key is None


def test_generation_controls_default_to_bounded_deterministic_values():
    from app.core.settings import Settings

    settings = Settings(_env_file=None)

    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_retries == 2
    assert settings.llm_max_output_tokens == 1024
    assert settings.llm_temperature == 0.0
    assert settings.llm_input_usd_per_million is None
    assert settings.tracing_capture_content is False
    assert settings.tracing_provider == "none"


def test_settings_local_cors_defaults_and_explicit_override():
    from app.core.settings import Settings

    local = Settings(_env_file=None)
    assert "http://127.0.0.1:3000" in local.cors_origin_list()
    production = Settings(environment="production", _env_file=None)
    assert production.cors_origin_list() == []
    named = Settings(cors_origins="https://app.example", environment="production", _env_file=None)
    assert named.cors_origin_list() == ["https://app.example"]


def test_qdrant_timeout_is_whole_seconds_and_never_zero():
    """int(0.5) would be 0, which the Qdrant client reads as no timeout."""
    import pytest
    from pydantic import ValidationError

    from app.core.settings import Settings

    assert isinstance(get_settings().qdrant_timeout_seconds, int)
    with pytest.raises(ValidationError):
        Settings(qdrant_timeout_seconds=0)
