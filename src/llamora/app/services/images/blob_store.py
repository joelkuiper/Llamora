"""Encrypted image files on disk.

Layout::

    <root>/<sha256(user_id)[:32]>/<image_id[-2:]>/<image_id>.<variant>

Directory and file names say nothing about users or content. Files are
written to a temporary name, fsynced and renamed into place, so a file
either exists completely or not at all. Callers write files *before* the
database row and delete them *after* it: a crash can leave a file without a
row (the sweeper removes those), never a row without its files.

Filesystem and file format only: no database, no DEK. Blocking I/O runs in
worker threads, one chunk at a time.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import BinaryIO

from llamora.app.services.images import VARIANTS, ImageDecryptError
from llamora.app.services.images.format import (
    SEALED_CHUNK,
    Decryptor,
    encrypt_chunks,
    file_aad,
)

logger = getLogger(__name__)

TMP_SUFFIX = ".tmp"


def user_dir_name(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class StoredFile:
    path: Path
    image_id: str
    mtime: float
    temporary: bool


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- layout ---------------------------------------------------------

    def ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def user_dir(self, user_id: str) -> Path:
        return self.root / user_dir_name(user_id)

    def path_for(self, user_id: str, image_id: str, variant: str) -> Path:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}")
        if not image_id or not image_id.isalnum():
            raise ValueError("image ids are alphanumeric")
        return self.user_dir(user_id) / image_id[-2:] / f"{image_id}.{variant}"

    # -- writing --------------------------------------------------------

    async def write(
        self, user_id: str, image_id: str, variant: str, key: bytes, data: bytes
    ) -> int:
        """Encrypt ``data`` into place; returns the file's size on disk."""

        path = self.path_for(user_id, image_id, variant)
        aad = file_aad(user_id, image_id, variant)
        return await asyncio.to_thread(self._write_sync, path, key, aad, data)

    def _write_sync(self, path: Path, key: bytes, aad: bytes, data: bytes) -> int:
        self.ensure_root()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(path.name + TMP_SUFFIX)
        size = 0
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                for piece in encrypt_chunks(key, aad, data):
                    fh.write(piece)
                    size += len(piece)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(path.parent)
        return size

    # -- reading --------------------------------------------------------

    async def open_stream(
        self, user_id: str, image_id: str, variant: str, key: bytes
    ) -> AsyncIterator[bytes]:
        """Open a variant for streaming decryption.

        The first chunk is read and verified *before* this returns, so a
        missing, misplaced or tampered file raises :class:`ImageDecryptError`
        here, before a response has started. A problem later in the file
        raises from the iterator.
        """

        path = self.path_for(user_id, image_id, variant)
        decryptor = Decryptor(key, file_aad(user_id, image_id, variant))
        try:
            fh = await asyncio.to_thread(path.open, "rb")
        except FileNotFoundError:
            raise ImageDecryptError("file is missing") from None
        try:
            first: list[bytes] = []
            while not first:
                data = await asyncio.to_thread(fh.read, SEALED_CHUNK)
                if not data:
                    first = decryptor.finish()
                    break
                first = decryptor.feed(data)
        except BaseException:
            await asyncio.to_thread(fh.close)
            raise
        return self._stream(fh, decryptor, first)

    async def _stream(
        self, fh: BinaryIO, decryptor: Decryptor, first: list[bytes]
    ) -> AsyncIterator[bytes]:
        try:
            for chunk in first:
                if chunk:
                    yield chunk
            while not decryptor.done:
                data = await asyncio.to_thread(fh.read, SEALED_CHUNK)
                chunks = decryptor.feed(data) if data else decryptor.finish()
                for chunk in chunks:
                    if chunk:
                        yield chunk
            if await asyncio.to_thread(fh.read, 1):
                raise ImageDecryptError("data after the final chunk")
        finally:
            await asyncio.to_thread(fh.close)

    async def read(
        self, user_id: str, image_id: str, variant: str, key: bytes
    ) -> bytes:
        """The whole decrypted variant (tests and small files)."""

        stream = await self.open_stream(user_id, image_id, variant, key)
        return b"".join([chunk async for chunk in stream])

    # -- deleting -------------------------------------------------------

    async def delete_image(self, user_id: str, image_id: str) -> None:
        await asyncio.to_thread(self._delete_image_sync, user_id, image_id)

    def _delete_image_sync(self, user_id: str, image_id: str) -> None:
        for variant in VARIANTS:
            path = self.path_for(user_id, image_id, variant)
            path.unlink(missing_ok=True)
            path.with_name(path.name + TMP_SUFFIX).unlink(missing_ok=True)

    async def delete_user(self, user_id: str) -> None:
        directory = self.user_dir(user_id)
        await asyncio.to_thread(shutil.rmtree, directory, ignore_errors=True)

    async def delete_file(self, path: Path) -> None:
        """Remove a file found by :meth:`list_files` (stray cleanup)."""

        resolved = path.resolve()
        if self.root.resolve() not in resolved.parents:
            raise ValueError(f"{path} is outside the image store")
        await asyncio.to_thread(resolved.unlink, missing_ok=True)

    # -- scanning -------------------------------------------------------

    async def list_files(self) -> list[StoredFile]:
        return await asyncio.to_thread(self._list_files_sync)

    def _list_files_sync(self) -> list[StoredFile]:
        files: list[StoredFile] = []
        if not self.root.is_dir():
            return files
        for user_dir in _subdirs(self.root):
            for shard in _subdirs(user_dir):
                with os.scandir(shard) as entries:
                    for entry in entries:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        name = entry.name
                        temporary = name.endswith(TMP_SUFFIX)
                        base = name[: -len(TMP_SUFFIX)] if temporary else name
                        image_id = base.split(".", 1)[0]
                        try:
                            mtime = entry.stat(follow_symlinks=False).st_mtime
                        except FileNotFoundError:
                            continue
                        files.append(
                            StoredFile(Path(entry.path), image_id, mtime, temporary)
                        )
        return files


def _subdirs(path: Path) -> list[Path]:
    with os.scandir(path) as entries:
        return [
            Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)
        ]


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        logger.debug("fsync not supported on directory %s", directory)
    finally:
        os.close(fd)
