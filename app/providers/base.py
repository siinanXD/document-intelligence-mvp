"""Provider interfaces.

These are the only seams through which the application reaches an external AI
system. Vendor SDKs may be imported by implementations of these interfaces and
nowhere else.
"""

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


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
    async def complete(self, system: str, user: str) -> str:
        """Return a plain-text completion."""

    @abstractmethod
    async def complete_structured(self, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        """Return a completion parsed into `schema`.

        Implementations must not invent values: fields without grounded
        evidence stay null or empty.
        """
