"""Shared fixtures.

The database fixtures build the schema by running the real Alembic migrations
rather than `create_all`, so every run also exercises the path a deployment
takes. Each test then works inside a transaction that is rolled back, which
keeps tests isolated without re-migrating for each one.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import reset_engine
from app.core.qdrant import reset_qdrant_client
from app.core.settings import get_settings
from app.main import create_app
from app.models import Document, DocumentStatus, Tenant
from app.providers.registry import get_embedding_provider, get_llm_provider
from tests.db import REQUIRE_DB, TEST_DATABASE_URL


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


def _run_migrations() -> None:
    """Run Alembic against the test database.

    Called from a worker thread: migrations/env.py drives the async engine with
    asyncio.run(), which refuses to start inside an event loop that is already
    running - and the fixture calling this is async.
    """
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:
        await engine.dispose()
        message = f"PostgreSQL not reachable at TEST_DATABASE_URL: {type(exc).__name__}"
        # In CI a missing database is a failure, not a reason to quietly pass.
        # Locally it is a skip, so contributors without PostgreSQL can work.
        if REQUIRE_DB:
            pytest.fail(f"{message} (REQUIRE_DB=1)")
        pytest.skip(message)

    await asyncio.to_thread(_run_migrations)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """A session inside a transaction that is always rolled back."""
    connection = await db_engine.connect()
    transaction = await connection.begin()
    # join_transaction_mode="create_savepoint" keeps the session's own
    # rollbacks - including the implicit one after an IntegrityError - inside a
    # SAVEPOINT, so they cannot unwind the outer transaction this fixture owns.
    session = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )()
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


async def _make_tenant(session, prefix: str) -> Tenant:
    tenant = Tenant(slug=f"{prefix}-{uuid.uuid4().hex[:8]}", name=prefix.title())
    session.add(tenant)
    await session.flush()
    return tenant


@pytest_asyncio.fixture
async def tenant(db_session) -> Tenant:
    return await _make_tenant(db_session, "acme")


@pytest_asyncio.fixture
async def other_tenant(db_session) -> Tenant:
    return await _make_tenant(db_session, "globex")


@pytest.fixture
def make_document(db_session):
    """Build a document for a given tenant, with sane defaults."""

    async def _make(tenant, *, file_hash: str | None = None, **overrides) -> Document:
        document = Document(
            tenant_id=tenant.id,
            filename=overrides.pop("filename", "contract.pdf"),
            mime_type=overrides.pop("mime_type", "application/pdf"),
            storage_key=overrides.pop("storage_key", f"{tenant.id}/{uuid.uuid4()}"),
            file_hash=file_hash or uuid.uuid4().hex + uuid.uuid4().hex,
            status=overrides.pop("status", DocumentStatus.pending),
            **overrides,
        )
        db_session.add(document)
        await db_session.flush()
        return document

    return _make
