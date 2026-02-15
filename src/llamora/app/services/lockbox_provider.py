from __future__ import annotations

from typing import Any, Protocol

from llamora.app.services.lockbox import Lockbox
from llamora.app.services.lockbox_store import LockboxStore


class HasPool(Protocol):
    pool: Any


_lockbox_store: LockboxStore | None = None
_lockbox_pool: Any | None = None


def get_lockbox_store_for_db(db: HasPool) -> LockboxStore:
    if db.pool is None:
        raise RuntimeError("Database pool is not initialized")

    global _lockbox_store, _lockbox_pool
    if _lockbox_store is None or db.pool is not _lockbox_pool:
        _lockbox_store = LockboxStore(Lockbox(db.pool))
        _lockbox_pool = db.pool
    return _lockbox_store


__all__ = ["get_lockbox_store_for_db"]
