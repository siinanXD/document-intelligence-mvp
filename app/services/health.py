"""Dependency probes for the readiness check.

Probes are cheap, time-bounded and never surface connection strings,
credentials or driver messages to the caller: the API reports up or down, the
detail stays in the logs.
"""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.core.settings import get_settings
from app.services.vector_store import VectorStoreService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    healthy: bool


async def check_database() -> DependencyStatus:
    timeout = get_settings().health_check_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("database health check failed", exc_info=False)
        return DependencyStatus(name="database", healthy=False)
    return DependencyStatus(name="database", healthy=True)


async def check_vector_store() -> DependencyStatus:
    timeout = get_settings().health_check_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            healthy = await VectorStoreService().ping()
    except Exception:
        logger.warning("vector store health check failed", exc_info=False)
        return DependencyStatus(name="vector_store", healthy=False)
    return DependencyStatus(name="vector_store", healthy=healthy)


async def check_dependencies() -> list[DependencyStatus]:
    """Probe every dependency concurrently."""
    return list(await asyncio.gather(check_database(), check_vector_store()))
