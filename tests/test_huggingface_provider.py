"""Hugging Face / TEI-compatible providers.

Every HTTP call is faked; no test may reach the network.
"""

import pytest

from app.providers.base import ProviderResponseError
from app.providers.huggingface_provider import (
    HuggingFaceEmbeddingProvider,
    HuggingFaceReranker,
)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeHTTPClient:
    """Records posts and answers them from a scripted queue or a handler."""

    def __init__(self, responses=None, handler=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._responses = list(responses or [])
        self._handler = handler

    async def post(self, path: str, json: dict) -> _FakeResponse:
        self.calls.append((path, json))
        if self._handler is not None:
            return self._handler(path, json)
        return self._responses.pop(0)


def _embed_handler(dimensions: int = 4):
    def _handle(path, json):
        assert path == "/embed"
        vectors = [[float(i)] + [0.0] * (dimensions - 1) for i in range(len(json["inputs"]))]
        return _FakeResponse(vectors)

    return _handle


def _provider(client, **overrides) -> HuggingFaceEmbeddingProvider:
    kwargs = {"model": "BAAI/bge-m3", "dimensions": 4, "version": "v1", "batch_size": 32}
    kwargs.update(overrides)
    return HuggingFaceEmbeddingProvider(client, **kwargs)


async def test_embed_returns_one_vector_per_input_in_order():
    client = _FakeHTTPClient(handler=_embed_handler())
    provider = _provider(client)

    vectors = await provider.embed(["alpha", "beta", "gamma"])

    assert len(vectors) == 3
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]
    assert client.calls == [("/embed", {"inputs": ["alpha", "beta", "gamma"]})]


async def test_embed_splits_long_input_into_batches():
    client = _FakeHTTPClient(handler=_embed_handler())
    provider = _provider(client, batch_size=2)

    vectors = await provider.embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert [json["inputs"] for _, json in client.calls] == [["a", "b"], ["c"]]


async def test_embed_skips_the_call_for_no_input():
    client = _FakeHTTPClient()

    assert await _provider(client).embed([]) == []
    assert client.calls == []


def test_provider_exposes_its_identity():
    provider = _provider(
        _FakeHTTPClient(), model="Qwen/Qwen3-Embedding-0.6B", dimensions=1024, version="v2"
    )

    assert provider.provider == "huggingface"
    assert provider.model == "Qwen/Qwen3-Embedding-0.6B"
    assert provider.version == "v2"
    assert provider.dimensions == 1024


def test_provider_requires_model_and_positive_dimensions():
    with pytest.raises(ValueError, match="model must be set"):
        _provider(_FakeHTTPClient(), model="")
    with pytest.raises(ValueError, match="dimensions"):
        _provider(_FakeHTTPClient(), dimensions=0)


async def test_embed_rejects_a_dimension_mismatch_before_any_write():
    """A wrong HUGGINGFACE_EMBEDDING_DIMENSIONS fails loudly at the provider."""
    client = _FakeHTTPClient(handler=_embed_handler(dimensions=6))
    provider = _provider(client, dimensions=4)

    with pytest.raises(ProviderResponseError, match="expected 4"):
        await provider.embed(["alpha"])


async def test_embed_rejects_a_vector_count_mismatch():
    client = _FakeHTTPClient(responses=[_FakeResponse([[0.0, 0.0, 0.0, 0.0]])])
    provider = _provider(client)

    with pytest.raises(ProviderResponseError, match="returned 1 vectors for 2 inputs"):
        await provider.embed(["alpha", "beta"])


async def test_embed_rejects_a_non_list_response():
    client = _FakeHTTPClient(responses=[_FakeResponse({"error": "model overloaded"})])

    with pytest.raises(ProviderResponseError, match="non-list"):
        await _provider(client).embed(["alpha"])


async def test_embed_surfaces_http_errors():
    client = _FakeHTTPClient(responses=[_FakeResponse(None, status_code=503)])

    with pytest.raises(RuntimeError, match="HTTP 503"):
        await _provider(client).embed(["alpha"])


async def test_reranker_maps_scores_back_to_input_order():
    # TEI answers sorted by score, not by input position.
    client = _FakeHTTPClient(
        responses=[
            _FakeResponse(
                [
                    {"index": 2, "score": 0.9},
                    {"index": 0, "score": 0.4},
                    {"index": 1, "score": 0.1},
                ]
            )
        ]
    )
    reranker = HuggingFaceReranker(client, model="BAAI/bge-reranker-v2-m3")

    scores = await reranker.rerank("query", ["a", "b", "c"])

    assert scores == [0.4, 0.1, 0.9]
    assert client.calls == [("/rerank", {"query": "query", "texts": ["a", "b", "c"]})]


async def test_reranker_skips_the_call_for_no_texts():
    client = _FakeHTTPClient()

    assert await HuggingFaceReranker(client).rerank("query", []) == []
    assert client.calls == []


def test_reranker_exposes_its_identity():
    reranker = HuggingFaceReranker(_FakeHTTPClient(), model="BAAI/bge-reranker-v2-m3")

    assert reranker.provider == "huggingface"
    assert reranker.model == "BAAI/bge-reranker-v2-m3"


async def test_reranker_rejects_an_out_of_range_index():
    client = _FakeHTTPClient(responses=[_FakeResponse([{"index": 5, "score": 0.9}])])

    with pytest.raises(ProviderResponseError, match="out-of-range"):
        await HuggingFaceReranker(client).rerank("query", ["a", "b"])


async def test_reranker_rejects_missing_scores():
    client = _FakeHTTPClient(responses=[_FakeResponse([{"index": 0, "score": 0.9}])])

    with pytest.raises(ProviderResponseError, match="scored 1 of 2"):
        await HuggingFaceReranker(client).rerank("query", ["a", "b"])


async def test_reranker_rejects_a_non_numeric_score():
    client = _FakeHTTPClient(responses=[_FakeResponse([{"index": 0, "score": "high"}])])

    with pytest.raises(ProviderResponseError, match="non-numeric"):
        await HuggingFaceReranker(client).rerank("query", ["a"])


async def test_reranker_rejects_a_non_list_response():
    client = _FakeHTTPClient(responses=[_FakeResponse({"detail": "bad request"})])

    with pytest.raises(ProviderResponseError, match="non-list"):
        await HuggingFaceReranker(client).rerank("query", ["a"])
