"""Cache infrastructure for tag recall summaries.

This module provides caching for LLM-generated tag summaries with support
for tag-based invalidation when tag assignments change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from cachetools import LRUCache

from llamora.app.db.events import ENTRY_TAGS_CHANGED_EVENT, RepositoryEventBus

if TYPE_CHECKING:
    from llamora.app.db.entries import EntriesRepository

# Cache key: (user_id, tag_hash_hex, text_digest, max_chars)
CacheKey = tuple[str, str, str, int]

_SUMMARY_CACHE_LIMIT_FALLBACK = 512


class TagRecallSummaryCache:
    """Cache LLM-generated summaries keyed by tag hash.

    Uses cachetools.LRUCache for automatic eviction while maintaining
    a secondary index for tag-based invalidation.
    """

    __slots__ = ("_entries", "_by_tag", "_maxsize")

    def __init__(self, maxsize: int = _SUMMARY_CACHE_LIMIT_FALLBACK) -> None:
        self._maxsize = max(1, maxsize)
        self._entries: LRUCache[CacheKey, str] = LRUCache(maxsize=self._maxsize)
        self._by_tag: dict[tuple[str, str], set[CacheKey]] = {}

    def get(self, key: CacheKey) -> str | None:
        return self._entries.get(key)

    def set(
        self, key: CacheKey, summary: str, *, max_entries: int | None = None
    ) -> None:
        # Resize cache if max_entries differs from current maxsize
        if max_entries is not None and max_entries > 0 and max_entries != self._maxsize:
            self._maxsize = max_entries
            # Create new cache with updated size, preserving recent entries
            old_entries = list(self._entries.items())
            self._entries = LRUCache(maxsize=self._maxsize)
            for k, v in old_entries[-self._maxsize :]:
                self._entries[k] = v

        self._entries[key] = summary
        user_id, tag_hash_hex, *_ = key
        self._by_tag.setdefault((user_id, tag_hash_hex), set()).add(key)

    def invalidate_tag(self, user_id: str, tag_hash_hex: str) -> None:
        """Remove all cached entries for a specific tag."""
        tag_key = (user_id, tag_hash_hex)
        keys = self._by_tag.pop(tag_key, set())
        for key in keys:
            self._entries.pop(key, None)


# Module-level singleton cache instance
TAG_RECALL_SUMMARY_CACHE = TagRecallSummaryCache()


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
        self._cache.invalidate_tag(user_id, tag_hash_hex)


__all__ = [
    "CacheKey",
    "TagRecallSummaryCache",
    "TagRecallCacheSynchronizer",
    "TAG_RECALL_SUMMARY_CACHE",
]
