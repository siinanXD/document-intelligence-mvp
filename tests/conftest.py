import pytest
from app.core.db import reset_engine
from app.core.qdrant import reset_qdrant_client
from app.core.settings import get_settings
from app.main import create_app
from app.providers.registry import get_embedding_provider, get_llm_provider
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_process_state():
    """No cached settings, engine, client or provider leaks between tests."""

    def _reset() -> None:
        get_settings.cache_clear()
        get_embedding_provider.cache_clear()
        get_llm_provider.cache_clear()
        reset_engine()
        reset_qdrant_client()

    _reset()
    yield
    _reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
