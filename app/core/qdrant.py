"""Qdrant client lifecycle.

Only index/retrieval services may use this module. API routes never talk to
Qdrant directly.
"""

import asyncio
import logging

from qdrant_client import AsyncQdrantClient

from app.core.settings import get_settings

logger = logging.getLogger(__name__)

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Return the process-wide async Qdrant client, building it on first use.

    Construction performs a blocking server-version check, so this must not be
    reached first from inside a request handler - that would run synchronous
    HTTP on the event loop. `warm_qdrant_client` gets there during startup.
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=settings.qdrant_timeout_seconds,
        )
    return _client


async def warm_qdrant_client() -> None:
    """Build the client off the request path, in a worker thread.

    A Qdrant that is down must not stop the application from starting: the
    readiness check reports that, and liveness stays up either way.
    """
    try:
        await asyncio.to_thread(get_qdrant_client)
    except Exception:
        logger.warning("could not construct the Qdrant client during startup")


async def close_qdrant_client() -> None:
    """Close the client and reset the singleton. Called on shutdown."""
    global _client
    if _client is not None:
        client, _client = _client, None
        await client.close()


def reset_qdrant_client() -> None:
    """Drop the singleton without closing it. For test isolation only."""
    global _client
    _client = None
