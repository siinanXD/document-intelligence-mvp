"""Provider interfaces.

These are the only seams through which the application reaches an external AI
system. Vendor SDKs may be imported by implementations of these interfaces and
nowhere else.
"""

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from app.providers.generation import GenerationResult
from app.providers.prompts import Prompt

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ProviderResponseError(RuntimeError):
    """Raised when a provider returns a response the caller cannot use."""


class EmbeddingProvider(ABC):
    """Turns text into vectors.

    Implementations must be deterministic about their identity: `provider`,
    `model` and `version` are persisted alongside indexed data so that a
    provider change can be detected and reindexed rather than silently mixed.
    """

    @property
    @abstractmethod
    def provider(self) -> str:
        """Stable provider identifier, e.g. `openai`."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier used for embedding."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Version marker for the embedding space."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector dimensionality produced by this provider."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts, returning one vector per input in the same order."""


class RerankerProvider(ABC):
    """Scores query-passage relevance for a second retrieval stage.

    Reranking is optional: retrieval must work identically without one, and a
    reranker can only reorder passages the first stage already surfaced - it
    never adds passages of its own.
    """

    @property
    @abstractmethod
    def provider(self) -> str:
        """Stable provider identifier, e.g. `huggingface`."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier used for reranking, if the endpoint names one."""

    @abstractmethod
    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        """Return one relevance score per text, in the same order as `texts`."""


class LLMProvider(ABC):
    """Generates text, optionally constrained to a Pydantic schema."""

    @property
    @abstractmethod
    def provider(self) -> str:
        """Stable provider identifier, e.g. `openai`."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier used for completions."""

    @abstractmethod
    async def complete(self, prompt: Prompt, user: str) -> GenerationResult[str]:
        """Return a plain-text completion with generation metadata."""

    @abstractmethod
    async def complete_structured(
        self, prompt: Prompt, user: str, schema: type[SchemaT]
    ) -> GenerationResult[SchemaT]:
        """Return a completion parsed into `schema`, with generation metadata.

        Implementations must not invent values: fields without grounded
        evidence stay null or empty. Structured content stays on
        `GenerationResult.content`; it is not flattened to a string.
        """
