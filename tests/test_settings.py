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


def test_credentials_default_to_unset():
    settings = get_settings()

    assert settings.openai_api_key is None
    assert settings.qdrant_api_key is None
