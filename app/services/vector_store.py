"""Vector store service.

All Qdrant access goes through this module: API routes never import the client,
and nothing else in the application knows Qdrant exists.

Two rules hold everywhere below.

**Qdrant is an index, not a source of truth.** A point carries identifiers and
nothing else - no chunk text, no filenames. Text is read back from PostgreSQL,
so losing or rebuilding the collection costs a reindex, never data, and a
vector database breach yields ids rather than customer documents.

**Every query filters by tenant.** The filter is built here rather than by
callers, so a caller cannot forget it. There is no method that searches without
one.
"""

import logging
from typing import Any
from uuid import UUID

from qdrant_client import models

from app.core.qdrant import get_qdrant_client
from app.core.settings import get_settings

logger = logging.getLogger(__name__)


class VectorStoreError(RuntimeError):
    """The vector store could not serve the request."""


class VectorStoreService:
    def __init__(self, client: Any | None = None, collection: str | None = None) -> None:
        settings = get_settings()
        self._client = client or get_qdrant_client()
        self._collection = collection or settings.qdrant_collection

    @property
    def collection(self) -> str:
        return self._collection

    async def ping(self) -> bool:
        """Return whether the vector store answers a cheap metadata call."""
        try:
            await self._client.get_collections()
        except Exception:
            # Log the failure without the URL or API key.
            logger.warning("vector store unreachable", extra={"collection": self._collection})
            return False
        return True

    async def ensure_collection(self, *, dimensions: int) -> bool:
        """Create the collection if it is missing. Returns whether it created it.

        One collection holds every tenant's points: a collection per tenant
        would multiply Qdrant's per-collection overhead by the customer count
        and make a cross-tenant query a matter of naming the wrong one, rather
        than of omitting a filter that is always applied.

        An existing collection with a different size is an error, not a
        recreation. Silently upserting into the wrong space would mix
        incompatible vectors; the operator recreates the collection and
        reindexes instead.
        """
        try:
            existing = await self._client.get_collections()
            names = {collection.name for collection in existing.collections}
            if self._collection in names:
                actual = await self.collection_dimensions()
                if actual is None:
                    raise VectorStoreError("could not read collection dimensions")
                if actual != dimensions:
                    raise VectorStoreError(
                        f"vector collection dimensions are {actual}, provider has {dimensions}"
                    )
                return False

            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(
                    size=dimensions, distance=models.Distance.COSINE
                ),
            )
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(
                f"could not prepare the collection ({type(exc).__name__})"
            ) from None

        # Payload indexes on the two fields every query filters by. Without
        # them Qdrant scans payloads instead of using the filter as an index.
        for field in ("tenant_id", "document_id"):
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                # Local mode has no payload indexes and older servers may
                # reject a duplicate. Neither changes correctness.
                logger.debug("could not create payload index", extra={"field": field})

        logger.info("created vector collection", extra={"collection": self._collection})
        return True

    async def upsert_chunks(
        self,
        *,
        tenant_id: UUID,
        document_id: UUID,
        points: list[tuple[UUID, list[float], str]],
    ) -> int:
        """Write one document's chunk vectors, replacing whatever was there.

        The point id is the chunk's own id, so re-indexing overwrites rather
        than accumulating. Points belonging to chunks that no longer exist are
        removed first - a reprocessed document has entirely new chunk ids, and
        without this the old vectors would answer searches forever.
        """
        await self.delete_document(tenant_id=tenant_id, document_id=document_id)

        if not points:
            return 0

        expected = len(points[0][1])
        if any(len(vector) != expected for _, vector, _ in points):
            raise VectorStoreError("cannot index mixed-dimension vectors for one document")

        structs = [
            models.PointStruct(
                id=str(chunk_id),
                vector=vector,
                payload={
                    "tenant_id": str(tenant_id),
                    "document_id": str(document_id),
                    "chunk_id": str(chunk_id),
                    "source_id": source_id,
                },
            )
            for chunk_id, vector, source_id in points
        ]

        try:
            await self._client.upsert(collection_name=self._collection, points=structs, wait=True)
        except Exception as exc:
            raise VectorStoreError(f"could not index chunks ({type(exc).__name__})") from None

        return len(structs)

    async def delete_document(self, *, tenant_id: UUID, document_id: UUID) -> None:
        """Remove every point for one document of one tenant.

        A missing collection is already the desired end state, so repeating a
        delete after the collection was never created (or was recreated empty)
        is success rather than an error.
        """
        await self._delete_matching(
            tenant_id=tenant_id,
            document_ids=[document_id],
            failure="could not remove document points",
        )

    async def delete_tenant(self, *, tenant_id: UUID) -> None:
        """Remove every point belonging to a tenant."""
        await self._delete_matching(tenant_id=tenant_id, failure="could not remove tenant points")

    async def _delete_matching(
        self,
        *,
        tenant_id: UUID,
        document_ids: list[UUID] | None = None,
        failure: str,
    ) -> None:
        try:
            await self._client.delete(
                collection_name=self._collection,
                points_selector=models.FilterSelector(
                    filter=self._filter(tenant_id=tenant_id, document_ids=document_ids)
                ),
                wait=True,
            )
        except Exception as exc:
            if await self._collection_is_absent():
                return
            raise VectorStoreError(f"{failure} ({type(exc).__name__})") from None

    async def collection_dimensions(self) -> int | None:
        """Return the collection's vector size, or None if it is not there."""
        try:
            info = await self._client.get_collection(self._collection)
        except Exception:
            return None
        vectors = getattr(getattr(info.config, "params", None), "vectors", None)
        size = getattr(vectors, "size", None)
        return int(size) if size is not None else None

    async def search(
        self,
        *,
        tenant_id: UUID,
        vector: list[float],
        limit: int,
        document_ids: list[UUID] | None = None,
    ) -> list[tuple[UUID, float]]:
        """Return (chunk_id, score) for the closest points within one tenant."""
        try:
            response = await self._client.query_points(
                collection_name=self._collection,
                query=vector,
                limit=limit,
                query_filter=self._filter(tenant_id=tenant_id, document_ids=document_ids),
                with_payload=True,
            )
        except Exception as exc:
            # A tenant that has indexed nothing yet is searching a collection
            # that does not exist. That is an empty result, not an outage - the
            # alternative is a new tenant's first question answering 503.
            #
            # Only that one case. If the store cannot be reached at all, the
            # probe fails too, and an outage must surface as an outage: a
            # caller told "no results" during one would report it as a wrong
            # answer, which is worse than an error because it looks like one.
            if await self._collection_is_absent():
                return []
            raise VectorStoreError(f"search failed ({type(exc).__name__})") from None

        results: list[tuple[UUID, float]] = []
        for point in response.points:
            payload = point.payload or {}
            chunk_id = payload.get("chunk_id") or point.id
            results.append((UUID(str(chunk_id)), float(point.score)))
        return results

    async def _collection_is_absent(self) -> bool:
        """True only when the store answered and the collection is not there.

        An unreachable store returns False, not True: "I could not ask" is not
        the same as "it is not there", and conflating them turns an outage into
        an empty result.
        """
        try:
            existing = await self._client.get_collections()
        except Exception:
            return False
        return self._collection not in {c.name for c in existing.collections}

    @staticmethod
    def _filter(*, tenant_id: UUID, document_ids: list[UUID] | None = None) -> models.Filter:
        """Build the filter every operation uses. Tenant is never optional."""
        conditions: list[models.Condition] = [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=str(tenant_id)))
        ]
        if document_ids:
            conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchAny(any=[str(one) for one in document_ids]),
                )
            )
        return models.Filter(must=conditions)


class DocumentVectorStore:
    """Document-level vectors, in a collection of their own.

    Separate from the chunk collection because the two answer different
    questions and have different lifetimes - a document keeps one vector, a
    document's chunks are replaced wholesale on every reprocess. Sharing one
    collection would mean every chunk search had to exclude document points,
    which is a filter someone eventually forgets.

    The same rules apply: identifiers only in the payload, and every query
    filtered by tenant.
    """

    def __init__(self, client: Any | None = None, collection: str | None = None) -> None:
        settings = get_settings()
        self._client = client or get_qdrant_client()
        self._collection = collection or settings.qdrant_documents_collection

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self, *, dimensions: int) -> bool:
        service = VectorStoreService(client=self._client, collection=self._collection)
        return await service.ensure_collection(dimensions=dimensions)

    async def upsert(self, *, tenant_id: UUID, document_id: UUID, vector: list[float]) -> None:
        """Write one document's vector, replacing whatever it had."""
        try:
            await self._client.upsert(
                collection_name=self._collection,
                points=[
                    models.PointStruct(
                        id=str(document_id),
                        vector=vector,
                        payload={
                            "tenant_id": str(tenant_id),
                            "document_id": str(document_id),
                        },
                    )
                ],
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(
                f"could not index the document vector ({type(exc).__name__})"
            ) from None

    async def delete(self, *, tenant_id: UUID, document_id: UUID) -> None:
        service = VectorStoreService(client=self._client, collection=self._collection)
        await service.delete_document(tenant_id=tenant_id, document_id=document_id)

    async def similar(
        self, *, tenant_id: UUID, vector: list[float], limit: int, exclude: UUID | None = None
    ) -> list[tuple[UUID, float]]:
        """Return (document_id, score) for the nearest documents of one tenant.

        `exclude` drops the document being compared, which is otherwise always
        its own best match.
        """
        try:
            response = await self._client.query_points(
                collection_name=self._collection,
                query=vector,
                # One extra, because the document itself is very likely the
                # first result and is then discarded.
                limit=limit + 1,
                query_filter=VectorStoreService._filter(tenant_id=tenant_id),
                with_payload=True,
            )
        except Exception as exc:
            service = VectorStoreService(client=self._client, collection=self._collection)
            if await service._collection_is_absent():
                return []
            raise VectorStoreError(
                f"document similarity search failed ({type(exc).__name__})"
            ) from None

        results: list[tuple[UUID, float]] = []
        for point in response.points:
            payload = point.payload or {}
            document_id = UUID(str(payload.get("document_id") or point.id))
            if exclude is not None and document_id == exclude:
                continue
            results.append((document_id, float(point.score)))
        return results[:limit]
