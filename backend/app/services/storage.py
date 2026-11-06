"""Storage for original uploaded files.

Files live under random keys outside any web root and are only ever served through the API
after an authorization check. ``StorageBackend`` lets an S3-compatible backend replace the
local one without touching callers.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

import aiofiles
import anyio

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,511}$")


class StorageBackend(Protocol):
    async def save_file(self, source: Path, key: str) -> None: ...
    def iter_bytes(self, key: str, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.objects_dir = self.root / "objects"
        self.tmp_dir = self.root / "tmp"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY_RE.fullmatch(key) or ".." in key.split("/"):
            raise ValueError("Invalid storage key")
        path = (self.objects_dir / key).resolve()
        if not path.is_relative_to(self.objects_dir):
            raise ValueError("Invalid storage key")
        return path

    async def save_file(self, source: Path, key: str) -> None:
        dest = self._path(key)

        def _move() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), dest)

        await anyio.to_thread.run_sync(_move)

    async def iter_bytes(self, key: str, chunk_size: int = 64 * 1024) -> AsyncIterator[bytes]:
        async with aiofiles.open(self._path(key), "rb") as fh:
            while chunk := await fh.read(chunk_size):
                yield chunk

    async def delete(self, key: str) -> None:
        path = self._path(key)

        def _delete() -> None:
            path.unlink(missing_ok=True)
            parent = path.parent
            # Remove now-empty per-document directories, never the objects root.
            while parent != self.objects_dir and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

        await anyio.to_thread.run_sync(_delete)

    async def exists(self, key: str) -> bool:
        return await anyio.to_thread.run_sync(self._path(key).is_file)

    def local_path(self, key: str) -> Path:
        """Filesystem path for in-process parsers (worker)."""
        return self._path(key)
