import pytest
from app.core.db import get_engine, get_sessionmaker
from app.core.qdrant import get_qdrant_client
from app.core.settings import get_settings
from app.main import create_app
from app.providers.registry import get_embedding_provider, get_llm_provider
from fastapi.testclient import TestClient

_CACHED = (
    get_settings,
    get_engine,
    get_sessionmaker,
    get_qdrant_client,
    get_embedding_provider,
    get_llm_provider,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """No cached engine, client or provider leaks between tests."""
    for cached in _CACHED:
        cached.cache_clear()
    yield
    for cached in _CACHED:
        cached.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
