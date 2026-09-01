"""Cockpit metadata, CORS and local tenant bootstrap."""

import httpx
import pytest_asyncio
from fastapi.testclient import TestClient

from app.api.dependencies import get_session
from app.main import create_app
from app.services.health import DependencyStatus


def test_cockpit_reports_providers_and_evaluation_without_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-should-not-leak")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:s3cret@db:5432/x")
    from app.core.settings import get_settings

    get_settings.cache_clear()

    async def _all_up():
        return [
            DependencyStatus(name="database", healthy=True),
            DependencyStatus(name="vector_store", healthy=True),
        ]

    monkeypatch.setattr("app.services.cockpit.check_dependencies", _all_up)
    with TestClient(create_app()) as client:
        response = client.get("/meta/cockpit")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["providers"]["embedding_provider"]
    assert body["providers"]["llm_provider"]
    assert body["providers"]["llm_configured"] is True
    assert body["evaluation"]["retrieval"]["gate_passed"] is True
    assert body["evaluation"]["generation"]["gate_passed"] is True
    assert "cases" not in (body["evaluation"]["generation"].get("summary") or {})
    text = response.text
    assert "sk-secret-should-not-leak" not in text
    assert "s3cret" not in text


def test_cors_allows_local_web_origin(client):
    response = client.options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


def test_cors_is_off_in_production_unless_configured(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with TestClient(create_app()) as production:
        response = production.options(
            "/health",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in response.headers


@pytest_asyncio.fixture
async def api(db_session):
    application = create_app()

    async def _session_override():
        yield db_session

    application.dependency_overrides[get_session] = _session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        yield client


async def test_dev_tenant_create_and_get_round_trip(api):
    created = await api.post("/dev/tenants", json={"slug": "demo", "name": "Demo"})
    assert created.status_code == 200
    body = created.json()
    assert body["created"] is True
    assert body["slug"] == "demo"
    again = await api.post("/dev/tenants", json={"slug": "demo", "name": "Demo"})
    assert again.json()["created"] is False
    assert again.json()["id"] == body["id"]
    fetched = await api.get("/dev/tenants/demo")
    assert fetched.json()["id"] == body["id"]


def test_dev_tenants_404_outside_local(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with TestClient(create_app()) as production:
        response = production.post("/dev/tenants", json={"slug": "demo", "name": "Demo"})
        assert response.status_code == 404
