"""Side-effect pipelines for tag mutations.

Each function takes explicit dependencies — no hidden event wiring.
Called from route handlers after repository mutations complete.
"""

from __future__ import annotations

from llamora.app.services.history_cache import HistoryCache
from llamora.app.services.lockbox import Lockbox
from llamora.app.services.lockbox_store import LockboxStore
from llamora.app.services.tag_recall_cache import invalidate_tag_recall


async def after_tag_changed(
    *,
    history_cache: HistoryCache | None,
    tag_recall_store: LockboxStore,
    user_id: str,
    entry_id: str,
    tag_hash: bytes | str,
    created_date: str | None,
    lockbox: Lockbox | None = None,
) -> None:
    """Side-effects for a single tag link/unlink."""
    if history_cache and created_date:
        await history_cache.invalidate(user_id, created_date)
    tag_hex = tag_hash.hex() if isinstance(tag_hash, bytes) else str(tag_hash)
    await invalidate_tag_recall(tag_recall_store, user_id, tag_hex)
    if lockbox:
        await lockbox.delete(user_id, "digest", f"tag:{tag_hex}")


async def after_tag_deleted(
    *,
    history_cache: HistoryCache | None,
    tag_recall_store: LockboxStore,
    user_id: str,
    tag_hash: bytes | str,
    affected_entries: list[tuple[str, str | None]],
    lockbox: Lockbox | None = None,
) -> None:
    """Side-effects for a bulk tag deletion. Batches by unique date."""
    dates_seen: set[str] = set()
    for _entry_id, created_date in affected_entries:
        if created_date and created_date not in dates_seen:
            dates_seen.add(created_date)
            if history_cache:
                await history_cache.invalidate(user_id, created_date)
    tag_hex = tag_hash.hex() if isinstance(tag_hash, bytes) else str(tag_hash)
    await invalidate_tag_recall(tag_recall_store, user_id, tag_hex)
    if lockbox:
        await lockbox.delete(user_id, "digest", f"tag:{tag_hex}")
