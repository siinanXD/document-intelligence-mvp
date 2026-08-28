"""OpenAI implementations of the provider interfaces.

The OpenAI SDK is imported here and nowhere else. Both classes accept an
injected client so tests can supply a fake and never make a network call.
"""

from typing import Any

from openai import AsyncOpenAI

from app.providers.base import EmbeddingProvider, LLMProvider, SchemaT


class ProviderResponseError(RuntimeError):
    """Raised when a provider returns a response the caller cannot use."""


# Dimensionality of the models we support. Unknown models must be declared
# explicitly rather than guessed, so an unsupported one fails loudly.
_MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        client: Any,
        model: str = "text-embedding-3-small",
        version: str = "v1",
        batch_size: int = 128,
    ) -> None:
        if model not in _MODEL_DIMENSIONS:
            raise ValueError(f"unknown embedding model: {model}")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._client = client
        self._model = model
        self._version = version
        self._batch_size = batch_size

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return _MODEL_DIMENSIONS[self._model]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, preserving input order.

        A long document produces more chunks than one request should carry, so
        the input is split. Batches run in sequence rather than concurrently:
        the provider rate-limits per minute, and a burst of parallel requests
        buys latency at the cost of being throttled.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.embeddings.create(model=self._model, input=batch)
            # The API preserves input order, but sorting by index makes that
            # explicit rather than assumed.
            items = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in items)

        if len(vectors) != len(texts):
            raise ProviderResponseError(
                f"{self._model} returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors


class OpenAILLMProvider(LLMProvider):
    def __init__(self, client: Any, model: str = "gpt-4o-mini") -> None:
        self._client = client
        self._model = model

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, system: str, user: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    async def complete_structured(self, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        response = await self._client.chat.completions.parse(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=schema,
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            # A refusal or an unparseable response arrives as parsed=None.
            # Returning it would defer the failure to a far-away call site.
            raise ProviderResponseError(f"{self._model} returned no parsable {schema.__name__}")
        return parsed


def build_openai_client(api_key: str) -> AsyncOpenAI:
    """Construct a real OpenAI client. Never called from tests."""
    return AsyncOpenAI(api_key=api_key)
