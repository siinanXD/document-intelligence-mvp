"""Object storage interface.

Implementations (local filesystem for development, S3-compatible in production)
land with the upload work; this module defines the seam they must satisfy.
"""

from abc import ABC, abstractmethod


class ObjectNotFoundError(Exception):
    """Raised when a storage key does not exist."""


class StorageBackend(ABC):
    """Binary object storage addressed by an opaque, tenant-scoped key."""

    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Store `data` under `key`, overwriting any existing object."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Return the object stored under `key`.

        Raises `ObjectNotFoundError` when the key does not exist.
        """

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove `key`. Deleting a missing key is not an error."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return whether `key` currently holds an object."""
