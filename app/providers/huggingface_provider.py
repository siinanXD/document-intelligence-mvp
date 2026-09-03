"""Hugging Face / Text Embeddings Inference-compatible providers.

Speaks the TEI HTTP protocol (`POST /embed`, `POST /rerank`) against a
configurable base URL. Where that endpoint runs - a local container, a GPU box
outside Railway, or the hosted Hugging Face inference service - is a hosting
concern that never reaches this module: it receives an injected HTTP client
and a base URL, nothing more.

The served model is fixed by the endpoint, not chosen per request. The model
name configured here is an identity marker persisted with indexed data, so a
change of endpoint or model is detected and reindexed rather than silently
mixing embedding spaces.

httpx is imported here and nowhere else in the provider layer. Both classes
accept an injected client so tests can supply a fake and never touch the
network.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.providers.base import EmbeddingProvider, ProviderResponseError, RerankerProvider

logger = logging.getLogger(__name__)

PROVIDER_NAME = "huggingface"


class HuggingFaceEmbeddingProvider(EmbeddingProvider):
    """Embeddings from a TEI-compatible `/embed` endpoint.

    The endpoint cannot be asked which vector width the application expects,
    so the configured dimensionality is verified against every response.
    A mismatch fails here, before any write, rather than surfacing later as a
    corrupted collection.
    """

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        dimensions: int,
        version: str = "v1",
        batch_size: int = 32,
    ) -> None:
        if not model:
            raise ValueError("model must be set for the Hugging Face provider")
        if dimensions < 1:
            raise ValueError("dimensions must be at least 1")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._version = version
        self._batch_size = batch_size

    @property
    def provider(self) -> str:
        return PROVIDER_NAME

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, preserving input order.

        Batches run in sequence, matching the OpenAI provider: a burst of
        parallel requests against one inference endpoint buys queueing, not
        throughput.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.post("/embed", json={"inputs": batch})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ProviderResponseError(f"{self._model} returned a non-list embed response")
            if len(payload) != len(batch):
                raise ProviderResponseError(
                    f"{self._model} returned {len(payload)} vector(s) for a batch of "
                    f"{len(batch)} inputs"
                )
            vectors.extend(payload)

        if len(vectors) != len(texts):
            raise ProviderResponseError(
                f"{self._model} returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self._dimensions:
                actual = len(vector) if isinstance(vector, list) else "non-vector"
                raise ProviderResponseError(
                    f"{self._model} returned a {actual}-dimensional vector, "
                    f"expected {self._dimensions}; fix HUGGINGFACE_EMBEDDING_DIMENSIONS "
                    "and reindex"
                )
        return vectors


class HuggingFaceReranker(RerankerProvider):
    """Relevance scores from a TEI-compatible `/rerank` endpoint.

    The response is a list of ``{"index": i, "score": s}`` objects, usually
    sorted by score. It is mapped back to input order here so the caller can
    zip scores with the texts it sent.
    """

    def __init__(self, client: Any, *, model: str = "") -> None:
        self._client = client
        self._model = model

    @property
    def provider(self) -> str:
        return PROVIDER_NAME

    @property
    def model(self) -> str:
        return self._model

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []

        response = await self._client.post("/rerank", json={"query": query, "texts": texts})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ProviderResponseError("the reranker returned a non-list response")

        scores: list[float | None] = [None] * len(texts)
        for item in payload:
            index = item.get("index") if isinstance(item, dict) else None
            score = item.get("score") if isinstance(item, dict) else None
            if not isinstance(index, int) or not 0 <= index < len(texts):
                raise ProviderResponseError("the reranker returned an out-of-range index")
            if not isinstance(score, (int, float)):
                raise ProviderResponseError("the reranker returned a non-numeric score")
            scores[index] = float(score)

        if any(score is None for score in scores):
            raise ProviderResponseError(
                f"the reranker scored {sum(s is not None for s in scores)} of {len(texts)} texts"
            )
        return scores  # type: ignore[return-value]


def build_huggingface_client(
    base_url: str,
    *,
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
) -> httpx.AsyncClient:
    """Construct a real HTTP client for a TEI-compatible endpoint.

    Never called from tests; they inject a fake instead.
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=timeout_seconds,
    )
