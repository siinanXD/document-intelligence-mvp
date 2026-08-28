import pytest
from app import __version__
from app.main import create_app
from app.services.health import DependencyStatus
from fastapi.testclient import TestClient


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "environment": "local",
    }


def test_health_reports_configured_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "ci")

    with TestClient(create_app()) as client:
        assert client.get("/health").json()["environment"] == "ci"


def test_health_stays_up_when_dependencies_are_down(monkeypatch, client):
    """Liveness must not fail because a dependency is unreachable."""

    async def _all_down():
        return [
            DependencyStatus(name="database", healthy=False),
            DependencyStatus(name="vector_store", healthy=False),
        ]

    monkeypatch.setattr("app.api.health.check_dependencies", _all_down)

    assert client.get("/health").status_code == 200


def test_readiness_is_ready_when_all_dependencies_are_up(monkeypatch, client):
    async def _all_up():
        return [
            DependencyStatus(name="database", healthy=True),
            DependencyStatus(name="vector_store", healthy=True),
        ]

    monkeypatch.setattr("app.api.health.check_dependencies", _all_up)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["checks"] == {"database": "up", "vector_store": "up"}


@pytest.mark.parametrize("failing", ["database", "vector_store"])
def test_readiness_is_degraded_when_a_dependency_is_down(monkeypatch, client, failing):
    async def _one_down():
        return [
            DependencyStatus(name=name, healthy=name != failing)
            for name in ("database", "vector_store")
        ]

    monkeypatch.setattr("app.api.health.check_dependencies", _one_down)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"][failing] == "down"


def test_readiness_never_leaks_connection_details(monkeypatch, client):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:sup3rsecret@db:5432/x")
    monkeypatch.setenv("QDRANT_API_KEY", "qdrant-secret-key")

    async def _all_down():
        return [
            DependencyStatus(name="database", healthy=False),
            DependencyStatus(name="vector_store", healthy=False),
        ]

    monkeypatch.setattr("app.api.health.check_dependencies", _all_down)

    body = client.get("/health/ready").text

    assert "sup3rsecret" not in body
    assert "qdrant-secret-key" not in body
