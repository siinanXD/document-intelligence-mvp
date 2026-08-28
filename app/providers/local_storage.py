"""Local filesystem storage, for development and tests.

Keys are treated as untrusted input: every key is resolved and checked to stay
inside the configured root, so a key containing `..` cannot write outside it.
"""

import asyncio
import shutil
from pathlib import Path

from app.providers.storage import ObjectNotFoundError, StorageBackend


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path_for(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        # `..` in a key must not escape the root, whatever produced the key.
        if not candidate.is_relative_to(self._root):
            raise ValueError("storage key escapes the storage root")
        return candidate

    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._path_for(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling then move, so a reader never sees half an object.
            temporary = path.with_name(f".{path.name}.partial")
            temporary.write_bytes(data)
            temporary.replace(path)

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        path = self._path_for(key)

        def _read() -> bytes:
            try:
                return path.read_bytes()
            except FileNotFoundError as exc:
                raise ObjectNotFoundError(key) from exc

        return await asyncio.to_thread(_read)

    async def delete(self, key: str) -> None:
        path = self._path_for(key)

        def _delete() -> None:
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def exists(self, key: str) -> bool:
        path = self._path_for(key)
        return await asyncio.to_thread(path.is_file)

    async def clear(self) -> None:
        """Remove everything under the root. Development and tests only."""
        await asyncio.to_thread(shutil.rmtree, self._root, True)
