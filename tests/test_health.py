from app import __version__
from app.main import create_app
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
