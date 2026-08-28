"""Dependency probes: failures degrade, they never raise."""

import asyncio

import pytest

from app.services import health as health_service
from app.services.vector_store import VectorStoreService


class _FailingSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, statement):
        raise ConnectionRefusedError("no database here")


class _WorkingSession(_FailingSession):
    async def execute(self, statement):
        return None


class _HangingSession(_FailingSession):
    async def execute(self, statement):
        await asyncio.sleep(10)


def _sessionmaker(session):
    return lambda: session


async def test_database_probe_reports_up(monkeypatch):
    monkeypatch.setattr(
        health_service, "get_sessionmaker", lambda: _sessionmaker(_WorkingSession())
    )

    assert (await health_service.check_database()).healthy is True


async def test_database_probe_reports_down_instead_of_raising(monkeypatch):
    monkeypatch.setattr(
        health_service, "get_sessionmaker", lambda: _sessionmaker(_FailingSession())
    )

    status = await health_service.check_database()

    assert status.name == "database"
    assert status.healthy is False


async def test_database_probe_is_time_bounded(monkeypatch):
    monkeypatch.setenv("HEALTH_CHECK_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(
        health_service, "get_sessionmaker", lambda: _sessionmaker(_HangingSession())
    )

    assert (await health_service.check_database()).healthy is False


class _FakeQdrant:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self.calls = 0

    async def get_collections(self):
        self.calls += 1
        if self._fail:
            raise ConnectionError("no vector store here")
        return []


async def test_vector_store_probe_reports_up():
    client = _FakeQdrant()

    assert await VectorStoreService(client=client).ping() is True
    assert client.calls == 1


async def test_vector_store_probe_reports_down_instead_of_raising():
    assert await VectorStoreService(client=_FakeQdrant(fail=True)).ping() is False


async def test_vector_store_probe_reports_down_through_the_service(monkeypatch):
    monkeypatch.setattr(
        health_service, "VectorStoreService", lambda: VectorStoreService(_FakeQdrant(fail=True))
    )

    status = await health_service.check_vector_store()

    assert status.name == "vector_store"
    assert status.healthy is False


async def test_dependencies_are_probed_together(monkeypatch):
    monkeypatch.setattr(
        health_service, "get_sessionmaker", lambda: _sessionmaker(_WorkingSession())
    )
    monkeypatch.setattr(
        health_service, "VectorStoreService", lambda: VectorStoreService(_FakeQdrant())
    )

    statuses = await health_service.check_dependencies()

    assert [s.name for s in statuses] == ["database", "vector_store"]
    assert all(s.healthy for s in statuses)


@pytest.mark.parametrize("secret", ["sup3rsecret", "qdrant-secret-key"])
async def test_probe_failures_do_not_log_credentials(monkeypatch, caplog, secret):
    monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://user:{secret}@db:5432/x")
    monkeypatch.setenv("QDRANT_API_KEY", secret)
    monkeypatch.setattr(
        health_service, "get_sessionmaker", lambda: _sessionmaker(_FailingSession())
    )
    monkeypatch.setattr(
        health_service, "VectorStoreService", lambda: VectorStoreService(_FakeQdrant(fail=True))
    )

    with caplog.at_level("DEBUG"):
        await health_service.check_dependencies()

    assert secret not in caplog.text
