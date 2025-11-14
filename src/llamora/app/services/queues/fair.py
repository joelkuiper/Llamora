from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Hashable, Iterable, Mapping
from typing import Deque, Generic, TypeVar

logger = logging.getLogger(__name__)

_Owner = TypeVar("_Owner", bound=Hashable)
_T = TypeVar("_T")


class OwnerCapacityError(RuntimeError):
    """Raised when attempting to enqueue beyond an owner's capacity."""

    __slots__ = ("owner", "limit")

    def __init__(self, owner: _Owner, limit: int) -> None:
        self.owner = owner
        self.limit = limit
        super().__init__(f"Owner {owner!r} queue capacity reached ({limit})")


class FairAsyncQueue(Generic[_Owner, _T]):
    """Maintains fair round-robin scheduling across owners."""

    __slots__ = (
        "_buckets",
        "_order",
        "_id_getter",
        "_index",
        "_listeners",
        "_owner_limits",
        "_default_owner_limit",
        "_owner_counts",
        "_loop",
        "_not_empty",
        "_space_available",
    )

    def __init__(
        self,
        *,
        id_getter: Callable[[_T], Hashable],
        listeners: Iterable[Callable[["FairAsyncQueue[_Owner, _T]"], None]] | None = None,
        owner_limits: Mapping[_Owner, int] | None = None,
        default_owner_limit: int | None = None,
    ) -> None:
        self._buckets: dict[_Owner, Deque[_T]] = {}
        self._order: deque[_Owner] = deque()
        self._id_getter = id_getter
        self._index: dict[Hashable, _Owner] = {}
        self._listeners: set[Callable[["FairAsyncQueue[_Owner, _T]"], None]] = set(
            listeners or []
        )
        self._owner_limits = dict(owner_limits or {})
        self._default_owner_limit = default_owner_limit
        self._owner_counts: dict[_Owner, int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._not_empty = asyncio.Condition()
        self._space_available = asyncio.Condition()

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
        limit = self._resolve_owner_limit(owner)
        if limit is not None:
            queued = self._owner_counts.get(owner, 0)
            if queued >= limit:
                raise OwnerCapacityError(owner, limit)
        bucket = self._buckets.get(owner)
        if bucket is None:
            bucket = deque()
            self._buckets[owner] = bucket
            self._order.append(owner)
        bucket.append(item)
        self._index[item_id] = owner
        self._owner_counts[owner] = len(bucket)
        self._notify_listeners()
        self._notify_condition(self._not_empty)

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
                self._owner_counts[owner] = len(bucket)
            else:
                self._buckets.pop(owner, None)
                self._owner_counts.pop(owner, None)
            self._notify_listeners()
            self._notify_condition(self._space_available)
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
                if bucket:
                    self._owner_counts[owner] = len(bucket)
                else:
                    self._buckets.pop(owner, None)
                    self._order = deque(o for o in self._order if o != owner)
                    self._owner_counts.pop(owner, None)
                self._notify_listeners()
                self._notify_condition(self._space_available)
                return True
        return False

    def clear(self) -> None:
        if not self._index and not self._buckets:
            return
        self._index.clear()
        self._buckets.clear()
        self._order.clear()
        self._owner_counts.clear()
        self._notify_listeners()
        self._notify_condition(self._space_available)

    def __len__(self) -> int:
        return len(self._index)

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return bool(self._index)

    async def async_pop(self) -> _T:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        while True:
            async with self._not_empty:
                await self._not_empty.wait_for(lambda: bool(self))
            item = self.pop_next()
            if item is not None:
                return item

    async def wait_for_capacity(self, owner: _Owner) -> None:
        limit = self._resolve_owner_limit(owner)
        if limit is None:
            return
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        async with self._space_available:
            await self._space_available.wait_for(
                lambda: self._owner_counts.get(owner, 0) < limit
            )

    def _resolve_owner_limit(self, owner: _Owner) -> int | None:
        return self._owner_limits.get(owner, self._default_owner_limit)

    def _notify_listeners(self) -> None:
        if not self._listeners:
            return
        for callback in list(self._listeners):
            try:
                callback(self)
            except Exception:  # pragma: no cover - defensive
                logger.exception("FairAsyncQueue listener failed")

    def _notify_condition(self, condition: asyncio.Condition) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def _notify() -> None:
            async with condition:
                condition.notify_all()

        def _schedule() -> None:
            asyncio.create_task(_notify())

        loop.call_soon_threadsafe(_schedule)


