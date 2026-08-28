"""Vector store service.

All Qdrant access goes through this module. API routes never import the Qdrant
client. Collection management and search land with the indexing work; for now
this exposes only what the readiness check needs.
"""

import logging

from app.core.qdrant import get_qdrant_client
from app.core.settings import get_settings

logger = logging.getLogger(__name__)


class VectorStoreService:
    def __init__(self, client=None) -> None:
        self._client = client or get_qdrant_client()

    async def ping(self) -> bool:
        """Return whether the vector store answers a cheap metadata call."""
        settings = get_settings()
        try:
            await self._client.get_collections()
        except Exception:
            # Log the failure without the URL or API key.
            logger.warning(
                "vector store unreachable",
                extra={"collection": settings.qdrant_collection},
            )
            return False
        return True
