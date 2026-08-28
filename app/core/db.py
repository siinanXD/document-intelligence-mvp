"""SQLAlchemy 2.x async engine and session wiring."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


@lru_cache
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        pool_pre_ping=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that is always closed."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    """Release pooled connections. Called on application shutdown.

    Only disposes an engine that was actually built, so shutting down an app
    that never touched the database does not open a connection pool in order
    to close it.
    """
    if get_engine.cache_info().currsize == 0:
        return
    engine = get_engine()
    reset_engine()
    await engine.dispose()


def reset_engine() -> None:
    """Drop the cached engine and sessionmaker without disposing them."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
