"""Startup and shutdown behaviour."""

from fastapi.testclient import TestClient

from app.core import qdrant as qdrant_module
from app.main import create_app


def test_qdrant_client_is_built_during_startup(monkeypatch):
    """Constructing the client blocks on a version check.

    If the first construction happened inside a request handler it would run
    synchronous HTTP on the event loop, so startup must get there first.
    """
    built: list[str] = []

    def _build():
        built.append("constructed")
        return object()

    monkeypatch.setattr(qdrant_module, "get_qdrant_client", _build)

    with TestClient(create_app()):
        assert built == ["constructed"]


def test_startup_survives_a_qdrant_that_cannot_be_reached(monkeypatch):
    """A dependency being down must never stop the app from serving."""

    def _explode():
        raise ConnectionError("no vector store here")

    monkeypatch.setattr(qdrant_module, "get_qdrant_client", _explode)

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
