"""Lockbox helpers for tag recall summaries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from llamora.app.db.events import ENTRY_TAGS_CHANGED_EVENT, RepositoryEventBus
from llamora.app.services.lockbox_provider import get_lockbox_store_for_db
from llamora.app.services.lockbox_store import LockboxStore

if TYPE_CHECKING:
    from llamora.app.db.entries import EntriesRepository
    from llamora.persistence.local_db import LocalDB

CacheKey = str


def tag_recall_namespace(tag_hash_hex: str) -> str:
    return f"tag-recall:{tag_hash_hex}"


def get_tag_recall_store(db: "LocalDB") -> LockboxStore:
    return get_lockbox_store_for_db(db)


async def invalidate_tag_recall(
    store: LockboxStore, user_id: str, tag_hash_hex: str
) -> None:
    namespace = tag_recall_namespace(tag_hash_hex)
    await store.delete_namespace(user_id, namespace)


class TagRecallCacheSynchronizer:
    """Invalidate tag recall summaries when tag assignments change.

    This class subscribes to the ENTRY_TAGS_CHANGED_EVENT and invalidates
    cached summaries for affected tags. It skips invalidation for changes
    on the current day since those don't affect recall context.
    """

    __slots__ = ("_store", "_events", "_entries")

    def __init__(
        self,
        *,
        event_bus: RepositoryEventBus | None,
        entries_repository: "EntriesRepository | None",
        store: LockboxStore,
    ) -> None:
        self._store = store
        self._events = event_bus
        self._entries = entries_repository
        if not self._events:
            return
        self._events.subscribe(
            ENTRY_TAGS_CHANGED_EVENT,
            self._handle_tags_changed,
            background=True,
        )

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
        await invalidate_tag_recall(self._store, user_id, tag_hash_hex)


__all__ = [
    "CacheKey",
    "TagRecallCacheSynchronizer",
    "get_tag_recall_store",
    "invalidate_tag_recall",
    "tag_recall_namespace",
]
