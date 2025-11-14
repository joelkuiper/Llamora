from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Hashable, Iterable
from typing import Deque, Generic, TypeVar

logger = logging.getLogger(__name__)

_Owner = TypeVar("_Owner", bound=Hashable)
_T = TypeVar("_T")


class FairAsyncQueue(Generic[_Owner, _T]):
    """Maintains fair round-robin scheduling across owners."""

    __slots__ = (
        "_buckets",
        "_order",
        "_id_getter",
        "_index",
        "_listeners",
    )

    def __init__(
        self,
        *,
        id_getter: Callable[[_T], Hashable],
        listeners: Iterable[Callable[["FairAsyncQueue[_Owner, _T]"], None]] | None = None,
    ) -> None:
        self._buckets: dict[_Owner, Deque[_T]] = {}
        self._order: deque[_Owner] = deque()
        self._id_getter = id_getter
        self._index: dict[Hashable, _Owner] = {}
        self._listeners: set[Callable[["FairAsyncQueue[_Owner, _T]"], None]] = set(
            listeners or []
        )

    def add_listener(
        self, callback: Callable[["FairAsyncQueue[_Owner, _T]"], None]
    ) -> None:
        self._listeners.add(callback)

    def remove_listener(
        self, callback: Callable[["FairAsyncQueue[_Owner, _T]"], None]
    ) -> None:
        self._listeners.discard(callback)

    def enqueue(self, owner: _Owner, item: _T) -> None:
        item_id = self._id_getter(item)
        if item_id in self._index:
            logger.warning("Duplicate item %s scheduled; dropping existing entry", item_id)
            self.remove(item_id)
        bucket = self._buckets.get(owner)
        if bucket is None:
            bucket = deque()
            self._buckets[owner] = bucket
            self._order.append(owner)
        bucket.append(item)
        self._index[item_id] = owner
        self._notify_listeners()

    def pop_next(self) -> _T | None:
        while self._order:
            owner = self._order.popleft()
            bucket = self._buckets.get(owner)
            if not bucket:
                continue
            item = bucket.popleft()
            item_id = self._id_getter(item)
            self._index.pop(item_id, None)
            if bucket:
                self._order.append(owner)
            else:
                self._buckets.pop(owner, None)
            self._notify_listeners()
            return item
        return None

    def remove(self, item_id: Hashable) -> bool:
        owner = self._index.pop(item_id, None)
        if owner is None:
            return False
        bucket = self._buckets.get(owner)
        if not bucket:
            return False
        for idx, item in enumerate(bucket):
            if self._id_getter(item) == item_id:
                del bucket[idx]
                if not bucket:
                    self._buckets.pop(owner, None)
                    self._order = deque(o for o in self._order if o != owner)
                self._notify_listeners()
                return True
        return False

    def clear(self) -> None:
        if not self._index and not self._buckets:
            return
        self._index.clear()
        self._buckets.clear()
        self._order.clear()
        self._notify_listeners()

    def __len__(self) -> int:
        return len(self._index)

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return bool(self._index)

    def _notify_listeners(self) -> None:
        if not self._listeners:
            return
        for callback in list(self._listeners):
            try:
                callback(self)
            except Exception:  # pragma: no cover - defensive
                logger.exception("FairAsyncQueue listener failed")

