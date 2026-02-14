"""Lockbox-backed cache for tag recall summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from llamora.app.db.events import ENTRY_TAGS_CHANGED_EVENT, RepositoryEventBus
from llamora.app.services.lockbox import Lockbox, LockboxDecryptionError

if TYPE_CHECKING:
    from llamora.app.db.entries import EntriesRepository
    from llamora.persistence.local_db import LocalDB

CacheKey = str


class TagRecallSummaryCache:
    """Lockbox-backed cache keyed by tag hash."""

    __slots__ = ("_lockbox",)

    def __init__(self, lockbox: Lockbox) -> None:
        self._lockbox = lockbox

    @staticmethod
    def _namespace(tag_hash_hex: str) -> str:
        return f"tag-recall:{tag_hash_hex}"

    async def get(
        self, user_id: str, dek: bytes, tag_hash_hex: str, key: CacheKey
    ) -> str | None:
        try:
            value = await self._lockbox.get(
                user_id, dek, self._namespace(tag_hash_hex), key
            )
        except LockboxDecryptionError:
            return None
        if value is None:
            return None
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None

    async def set(
        self,
        user_id: str,
        dek: bytes,
        tag_hash_hex: str,
        key: CacheKey,
        summary: str,
        *,
        max_entries: int | None = None,
    ) -> None:
        if max_entries is not None and max_entries <= 0:
            return
        await self._lockbox.set(
            user_id,
            dek,
            self._namespace(tag_hash_hex),
            key,
            summary.encode("utf-8"),
        )

    async def invalidate_tag(self, user_id: str, tag_hash_hex: str) -> None:
        keys = await self._lockbox.list(user_id, self._namespace(tag_hash_hex))
        for key in keys:
            await self._lockbox.delete(user_id, self._namespace(tag_hash_hex), key)


_lockbox_cache: TagRecallSummaryCache | None = None
_lockbox_pool = None


def get_tag_recall_cache(db: "LocalDB") -> TagRecallSummaryCache:
    global _lockbox_cache, _lockbox_pool
    if _lockbox_cache is None or db.pool is not _lockbox_pool:
        _lockbox_cache = TagRecallSummaryCache(Lockbox(db.pool))
        _lockbox_pool = db.pool
    return _lockbox_cache


class TagRecallCacheSynchronizer:
    """Invalidate tag recall summaries when tag assignments change.

    This class subscribes to the ENTRY_TAGS_CHANGED_EVENT and invalidates
    cached summaries for affected tags. It skips invalidation for changes
    on the current day since those don't affect recall context.
    """

    __slots__ = ("_cache", "_events", "_entries")

    def __init__(
        self,
        *,
        event_bus: RepositoryEventBus | None,
        entries_repository: "EntriesRepository | None",
        cache: TagRecallSummaryCache,
    ) -> None:
        self._cache = cache
        self._events = event_bus
        self._entries = entries_repository
        if not self._events:
            return
        self._events.subscribe(ENTRY_TAGS_CHANGED_EVENT, self._handle_tags_changed)

    async def _handle_tags_changed(
        self,
        *,
        user_id: str,
        entry_id: str,
        tag_hash: bytes | str | None = None,
        created_date: str | None = None,
        client_today: str | None = None,
    ) -> None:
        if not tag_hash or not self._entries:
            return
        entry_date = created_date
        if not entry_date:
            entry_date = await self._entries.get_entry_date(user_id, entry_id)
        if not entry_date:
            return
        today_iso = client_today
        if not today_iso:
            today_iso = datetime.now(timezone.utc).date().isoformat()
        # Skip invalidation for same-day changes
        if entry_date == today_iso:
            return
        tag_hash_hex = tag_hash.hex() if isinstance(tag_hash, bytes) else str(tag_hash)
        await self._cache.invalidate_tag(user_id, tag_hash_hex)


__all__ = [
    "CacheKey",
    "TagRecallSummaryCache",
    "TagRecallCacheSynchronizer",
    "get_tag_recall_cache",
]
