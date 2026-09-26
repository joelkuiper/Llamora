"""Image attachments: upload, look up, stream, delete and sweep.

Ties the pieces together: :mod:`processing` (clean variants), the per-image
file key wrapped by the user's DEK (:class:`CryptoContext`), the encrypted
files (:class:`BlobStore`) and the rows (``db.images``). Linking images to
entries happens in the entries repository (``image_ids`` on append/update).
"""

from __future__ import annotations

import asyncio
import base64
import unicodedata
from collections.abc import AsyncIterator, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson
from nacl import utils
from nacl.exceptions import CryptoError
from ulid import ULID

from llamora.app.db.images import ImageRow
from llamora.app.services.crypto import CryptoContext
from llamora.app.services.images import (
    VARIANTS,
    ImageDecryptError,
    ImageRejected,
)
from llamora.app.services.images.blob_store import BlobStore
from llamora.app.services.images.processing import (
    ProcessedImage,
    encode_for_model,
    process,
)

if TYPE_CHECKING:
    from llamora.persistence.local_db import LocalDB

logger = getLogger(__name__)

TOO_MANY_PENDING = "too_many_pending"
MAX_FILENAME_CHARS = 255


@dataclass(frozen=True, slots=True)
class ImageConfig:
    root: Path
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 50_000_000
    max_per_entry: int = 8
    quality: int = 85
    processing_concurrency: int = 2
    pending_ttl: int = 24 * 60 * 60
    sweep_interval: int = 60 * 60
    sizes: Mapping[str, int] = field(
        default_factory=lambda: {"thumb": 480, "display": 2048, "full": 4096}
    )

    @property
    def max_pending(self) -> int:
        """Unsent uploads a user may have waiting (bounds abuse of storage)."""
        return self.max_per_entry * 4

    @classmethod
    def from_settings(cls, settings: Any) -> ImageConfig:
        """From ``settings.IMAGES`` (defaults in ``llamora.settings``); a
        relative path is relative to the working directory, like the DB."""

        cfg = settings.get("IMAGES") or {}
        sizes = cfg.get("sizes") or {}
        return cls(
            root=Path(str(cfg.get("path") or "images")).expanduser().resolve(),
            max_upload_bytes=int(cfg["max_upload_bytes"]),
            max_pixels=int(cfg["max_pixels"]),
            max_per_entry=int(cfg["max_per_entry"]),
            quality=int(cfg["quality"]),
            processing_concurrency=max(1, int(cfg["processing_concurrency"])),
            pending_ttl=int(cfg["pending_ttl"]),
            sweep_interval=int(cfg["sweep_interval"]),
            sizes={variant: int(sizes[variant]) for variant in VARIANTS},
        )


@dataclass(frozen=True, slots=True)
class VariantMeta:
    mime: str
    width: int
    height: int
    bytes: int


@dataclass(frozen=True, slots=True)
class ImageMeta:
    filename: str
    variants: dict[str, VariantMeta]

    def to_json(self) -> bytes:
        return orjson.dumps(
            {
                "filename": self.filename,
                "variants": {
                    name: {
                        "mime": v.mime,
                        "width": v.width,
                        "height": v.height,
                        "bytes": v.bytes,
                    }
                    for name, v in self.variants.items()
                },
            }
        )

    @classmethod
    def from_json(cls, raw: bytes) -> ImageMeta:
        data = orjson.loads(raw)
        return cls(
            filename=str(data.get("filename", "")),
            variants={
                name: VariantMeta(
                    mime=str(v["mime"]),
                    width=int(v["width"]),
                    height=int(v["height"]),
                    bytes=int(v["bytes"]),
                )
                for name, v in (data.get("variants") or {}).items()
            },
        )


@dataclass(frozen=True, slots=True)
class ImageRecord:
    id: str
    entry_id: str | None
    position: int
    meta: ImageMeta

    def ref(self) -> dict[str, Any]:
        """What entry payloads carry: the id and the display size."""
        display = self.meta.variants.get("display")
        return {
            "id": self.id,
            "width": display.width if display else 0,
            "height": display.height if display else 0,
        }


@dataclass(frozen=True, slots=True)
class SweepResult:
    rows: int = 0
    stray_files: int = 0


def clean_filename(name: str | None) -> str:
    """The upload's base name, without paths or control characters."""
    raw = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in raw if unicodedata.category(ch)[0] != "C").strip()
    return cleaned[:MAX_FILENAME_CHARS]


def _sql_timestamp(moment: datetime) -> str:
    """SQLite CURRENT_TIMESTAMP format (UTC)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ImageService:
    def __init__(self, db: LocalDB, store: BlobStore, config: ImageConfig) -> None:
        self._db = db
        self.store = store
        self.config = config
        self._processing = asyncio.Semaphore(config.processing_concurrency)
        self._sweep_task: asyncio.Task | None = None
        self._last_sweep: float | None = None

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        await asyncio.to_thread(self.store.ensure_root)
        self.nudge()

    async def stop(self) -> None:
        task = self._sweep_task
        self._sweep_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # -- upload ---------------------------------------------------------

    async def upload(
        self, ctx: CryptoContext, filename: str | None, data: bytes
    ) -> ImageRecord:
        """Store an upload as a pending image. Raises :class:`ImageRejected`."""

        ctx.require_write(operation="images.upload")
        if len(data) > self.config.max_upload_bytes:
            raise ImageRejected(ImageRejected.TOO_LARGE)
        if await self._db.images.count_pending(ctx.user_id) >= self.config.max_pending:
            raise ImageRejected(TOO_MANY_PENDING)

        async with self._processing:
            processed: ProcessedImage = await asyncio.to_thread(
                process,
                data,
                sizes=self.config.sizes,
                quality=self.config.quality,
                max_pixels=self.config.max_pixels,
                max_bytes=self.config.max_upload_bytes,
            )

        image_id = str(ULID())
        file_key = bytearray(utils.random(32))
        try:
            variants: dict[str, VariantMeta] = {}
            for name in VARIANTS:
                encoded = processed.variants[name]
                await self.store.write(
                    ctx.user_id, image_id, name, bytes(file_key), encoded.data
                )
                variants[name] = VariantMeta(
                    mime=encoded.mime,
                    width=encoded.width,
                    height=encoded.height,
                    bytes=len(encoded.data),
                )
            meta = ImageMeta(filename=clean_filename(filename), variants=variants)
            key_nonce, key_cipher, alg = ctx.wrap_image_key(image_id, bytes(file_key))
            meta_nonce, meta_cipher = ctx.encrypt_image_meta(image_id, meta.to_json())
            await self._db.images.insert_pending(
                ctx.user_id,
                image_id,
                key_nonce=key_nonce,
                key_cipher=key_cipher,
                meta_nonce=meta_nonce,
                meta_cipher=meta_cipher,
                alg=alg,
            )
            return ImageRecord(id=image_id, entry_id=None, position=0, meta=meta)
        except BaseException:
            await asyncio.shield(self.store.delete_image(ctx.user_id, image_id))
            raise
        finally:
            file_key[:] = bytes(len(file_key))

    # -- reading --------------------------------------------------------

    def _meta(self, ctx: CryptoContext, row: ImageRow) -> ImageMeta:
        try:
            raw = ctx.decrypt_image_meta(
                row.id, row.meta_nonce, row.meta_cipher, row.alg
            )
        except CryptoError as exc:
            raise ImageDecryptError(f"metadata of image {row.id}") from exc
        return ImageMeta.from_json(raw)

    def _record(self, ctx: CryptoContext, row: ImageRow) -> ImageRecord:
        return ImageRecord(
            id=row.id,
            entry_id=row.entry_id,
            position=row.position,
            meta=self._meta(ctx, row),
        )

    async def get(self, ctx: CryptoContext, image_id: str) -> ImageRecord | None:
        row = await self._db.images.get(ctx.user_id, image_id)
        return self._record(ctx, row) if row else None

    async def exists(self, user_id: str, image_id: str) -> bool:
        """Ownership check without touching keys (conditional requests)."""
        return await self._db.images.get(user_id, image_id) is not None

    async def open_variant(
        self, ctx: CryptoContext, image_id: str, variant: str
    ) -> tuple[VariantMeta, AsyncIterator[bytes]] | None:
        """A verified stream of one variant; None when it isn't the user's.

        Raises :class:`ImageDecryptError` when the image exists but its key,
        metadata or file does not check out.
        """

        if variant not in VARIANTS:
            return None
        row = await self._db.images.get(ctx.user_id, image_id)
        if row is None:
            return None
        meta = self._meta(ctx, row)
        variant_meta = meta.variants.get(variant)
        if variant_meta is None:
            raise ImageDecryptError(f"image {image_id} has no {variant} variant")
        try:
            file_key = bytearray(
                ctx.unwrap_image_key(row.id, row.key_nonce, row.key_cipher, row.alg)
            )
        except CryptoError as exc:
            raise ImageDecryptError(f"key of image {image_id}") from exc
        try:
            stream = await self.store.open_stream(
                ctx.user_id, image_id, variant, bytes(file_key)
            )
        finally:
            file_key[:] = bytes(len(file_key))
        return variant_meta, stream

    async def model_image_uri(
        self, ctx: CryptoContext, image_id: str, *, max_edge: int, quality: int = 85
    ) -> str | None:
        """An image as a vision model receives it: a JPEG data URI, shrunk
        to ``max_edge``. None when it isn't the user's or can't be read."""

        try:
            opened = await self.open_variant(ctx, image_id, "display")
            if opened is None:
                return None
            _, stream = opened
            data = b"".join([chunk async for chunk in stream])
            jpeg = await asyncio.to_thread(
                encode_for_model, data, max_edge=max_edge, quality=quality
            )
        except ImageDecryptError:
            logger.warning("Image %s can't be read for the model", image_id)
            return None
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")

    async def refs_for_entries(
        self, ctx: CryptoContext, entry_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Each entry's images (id and display size), in order: one query."""

        grouped = await self._db.images.get_for_entries(ctx.user_id, entry_ids)
        refs: dict[str, list[dict[str, Any]]] = {}
        for entry_id, rows in grouped.items():
            for row in rows:
                try:
                    refs.setdefault(entry_id, []).append(self._record(ctx, row).ref())
                except ImageDecryptError:
                    logger.warning("Skipping unreadable image %s", row.id)
        return refs

    async def attach_refs(
        self, ctx: CryptoContext, entries: Iterable[MutableMapping[str, Any]]
    ) -> None:
        """Set ``images`` on each user entry payload (one query for all)."""

        targets = [e for e in entries if e.get("id") and e.get("role") == "user"]
        refs = await self.refs_for_entries(ctx, [str(e["id"]) for e in targets])
        for entry in targets:
            entry["images"] = refs.get(str(entry["id"]), [])

    # -- deleting -------------------------------------------------------

    async def delete(self, ctx: CryptoContext, image_id: str) -> ImageRow | None:
        """Delete an image (pending or attached): row first, then files."""

        row = await self._db.images.delete(ctx.user_id, image_id)
        if row is not None:
            await self.store.delete_image(ctx.user_id, image_id)
        return row

    async def delete_user_files(self, user_id: str) -> None:
        """All of a user's files; call after the user's rows are deleted."""

        await self.store.delete_user(user_id)

    # -- sweeping -------------------------------------------------------

    async def sweep(self, *, now: datetime | None = None) -> SweepResult:
        """Remove orphans, expired pending uploads and files without rows."""

        moment = now or datetime.now(timezone.utc)
        cutoff = moment - timedelta(seconds=self.config.pending_ttl)
        rows = 0
        while True:
            batch = await self._db.images.collectable(
                pending_before=_sql_timestamp(cutoff)
            )
            if not batch:
                break
            deleted = set(await self._db.images.delete_detached([i for _, i in batch]))
            for user_id, image_id in batch:
                if image_id in deleted:
                    await self.store.delete_image(user_id, image_id)
            rows += len(deleted)
            if not deleted:
                break

        stray = 0
        files = await self.store.list_files()
        old = [f for f in files if f.mtime < cutoff.timestamp()]
        known = await self._db.images.existing_ids(f.image_id for f in old)
        for stored in old:
            if stored.temporary or stored.image_id not in known:
                await self.store.delete_file(stored.path)
                stray += 1

        self._last_sweep = moment.timestamp()
        if rows or stray:
            logger.info("Image sweep removed %d images and %d stray files", rows, stray)
        return SweepResult(rows=rows, stray_files=stray)

    def nudge(self) -> None:
        """Sweep soon, in the background (after deletes); never piles up."""

        if self._sweep_task is not None and not self._sweep_task.done():
            return
        self._sweep_task = asyncio.create_task(
            self._safe_sweep(), name="llamora-image-sweep"
        )

    async def maybe_sweep(self, *, now: datetime | None = None) -> None:
        """Called from the maintenance loop: sweep every ``sweep_interval``."""

        moment = now or datetime.now(timezone.utc)
        if (
            self._last_sweep is not None
            and moment.timestamp() - self._last_sweep < self.config.sweep_interval
        ):
            return
        self.nudge()

    async def _safe_sweep(self) -> None:
        try:
            await self.sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Image sweep failed")
