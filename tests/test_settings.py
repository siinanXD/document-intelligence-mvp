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
