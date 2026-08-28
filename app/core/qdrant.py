"""Qdrant client construction.

Only index/retrieval services may use this module. API routes never talk to
Qdrant directly.
"""

from functools import lru_cache

from qdrant_client import AsyncQdrantClient

from app.core.settings import get_settings


@lru_cache
def get_qdrant_client() -> AsyncQdrantClient:
    """Return the process-wide async Qdrant client."""
    settings = get_settings()
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=int(settings.qdrant_timeout_seconds),
    )


async def close_qdrant_client() -> None:
    """Close the client. Called on application shutdown."""
    if get_qdrant_client.cache_info().currsize:
        await get_qdrant_client().close()
        get_qdrant_client.cache_clear()
