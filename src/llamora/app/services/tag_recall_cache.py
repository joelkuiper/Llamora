"""Lockbox helpers for tag recall summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from llamora.app.services.lockbox_provider import get_lockbox_store_for_db
from llamora.app.services.lockbox_store import LockboxStore

if TYPE_CHECKING:
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


__all__ = [
    "CacheKey",
    "get_tag_recall_store",
    "invalidate_tag_recall",
    "tag_recall_namespace",
]
