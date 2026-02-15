from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from logging import getLogger
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from cachetools import TTLCache

from llamora.app.db.events import ENTRY_HISTORY_CHANGED_EVENT

logger = getLogger(__name__)

CacheKey = tuple[str, str]

if TYPE_CHECKING:
    from llamora.app.db.events import RepositoryEventBus
    from llamora.app.db.entries import EntriesRepository


@dataclass(slots=True)
class HistoryCacheEvent:
    name: str
    key: CacheKey
    payload: Any = None


HistoryCacheListener = Callable[[HistoryCacheEvent], None]

FrozenHistory = tuple[Mapping[str, Any], ...]


@dataclass(slots=True, frozen=True)
class HistoryCacheEntry:
    history: FrozenHistory | object
    revision: int


HistoryCacheBackend = MutableMapping[CacheKey, HistoryCacheEntry]


_INVALID_HISTORY_SENTINEL = object()


def _freeze_history(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
) -> FrozenHistory:
    frozen_entries: list[Mapping[str, Any]] = []
    for entry in history:
        raw_entry = dict(entry)
        tags = raw_entry.get("tags") or []
        raw_entry["tags"] = tuple(MappingProxyType(dict(tag)) for tag in tags)
        raw_entry["meta"] = MappingProxyType(dict(raw_entry.get("meta") or {}))
        frozen_entries.append(MappingProxyType(raw_entry))
    return tuple(frozen_entries)


def _thaw_history(history: FrozenHistory) -> list[dict[str, Any]]:
    thawed: list[dict[str, Any]] = []
    for entry in history:
        hydrated = dict(entry)
        hydrated["tags"] = [dict(tag) for tag in entry.get("tags", ())]
        hydrated["meta"] = dict(entry.get("meta", {}))
        thawed.append(hydrated)
    return thawed


def default_backend_factory(maxsize: int, ttl: int) -> HistoryCacheBackend:
    return TTLCache(maxsize=maxsize, ttl=ttl)


class HistoryCache:
    """Manage cached entry history with observer hooks."""

    __slots__ = ("_lock", "_backend", "_listeners")

    def __init__(
        self,
        *,
        maxsize: int,
        ttl: int,
        backend: HistoryCacheBackend | None = None,
        backend_factory: Callable[[int, int], HistoryCacheBackend] | None = None,
    ) -> None:
        if backend and backend_factory:
            raise ValueError("Provide either backend or backend_factory, not both")
        if backend is None:
            factory = backend_factory or default_backend_factory
            backend = factory(maxsize, ttl)
        assert backend is not None
        self._backend: HistoryCacheBackend = backend
        self._lock = asyncio.Lock()
        self._listeners: list[HistoryCacheListener] = []

    def add_listener(self, listener: HistoryCacheListener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: HistoryCacheListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            logger.debug("Attempted to remove unknown listener %r", listener)

    async def get(self, user_id: str, created_date: str) -> list[dict[str, Any]] | None:
        key = (user_id, created_date)
        async with self._lock:
            cached = self._backend.get(key)
        if cached is None or cached.history is _INVALID_HISTORY_SENTINEL:
            self._notify("miss", key)
            return None
        frozen = cast(FrozenHistory, cached.history)
        history = _thaw_history(frozen)
        self._notify(
            "hit", key, payload={"history": history, "revision": cached.revision}
        )
        return history

    async def store(
        self,
        user_id: str,
        created_date: str,
        history: Sequence[Mapping[str, Any] | dict[str, Any]],
        *,
        revision: int | None = None,
    ) -> None:
        key = (user_id, created_date)
        frozen = _freeze_history(history)
        async with self._lock:
            current = self._backend.get(key)
            if revision is not None and current and revision < current.revision:
                self._notify(
                    "store-rejected",
                    key,
                    payload={
                        "revision": revision,
                        "current_revision": current.revision,
                    },
                )
                return
            if revision is not None:
                next_revision = max(revision, current.revision if current else 0)
            else:
                next_revision = current.revision if current else 0
            self._backend[key] = HistoryCacheEntry(
                history=frozen, revision=next_revision
            )
        self._notify(
            "store",
            key,
            payload={"history": _thaw_history(frozen), "revision": next_revision},
        )

    async def append(
        self,
        user_id: str,
        created_date: str,
        entry: Mapping[str, Any],
        *,
        revision: int | None = None,
    ) -> None:
        key = (user_id, created_date)
        while True:
            async with self._lock:
                cached = self._backend.get(key)
            if cached is None or cached.history is _INVALID_HISTORY_SENTINEL:
                self._notify("append-skip", key, payload={"revision": revision})
                return
            if revision is not None and revision < cached.revision:
                self._notify(
                    "append-rejected",
                    key,
                    payload={"revision": revision, "current_revision": cached.revision},
                )
                return
            frozen = cast(FrozenHistory, cached.history)
            history = _thaw_history(frozen)
            new_entry = dict(entry)
            new_entry["tags"] = list(new_entry.get("tags", []))
            new_id = new_entry.get("id")
            inserted = False

            for idx, existing in enumerate(history):
                existing_id = existing.get("id")
                if existing_id == new_id:
                    history[idx] = new_entry
                    inserted = True
                    break
                if existing_id and new_id and existing_id > new_id:
                    history.insert(idx, new_entry)
                    inserted = True
                    break

            if not inserted:
                history.append(new_entry)

            updated = _freeze_history(history)
            async with self._lock:
                current = self._backend.get(key)
                if current is cached:
                    next_revision = (
                        revision if revision is not None else current.revision + 1
                    )
                    self._backend[key] = HistoryCacheEntry(
                        history=updated, revision=next_revision
                    )
                    self._notify(
                        "append",
                        key,
                        payload={"entry": dict(entry), "revision": next_revision},
                    )
                    return

    async def invalidate(
        self,
        user_id: str,
        created_date: str,
        *,
        revision: int | None = None,
    ) -> None:
        key = (user_id, created_date)
        async with self._lock:
            current = self._backend.get(key)
            current_revision = current.revision if current else 0
            if revision is not None and revision < current_revision:
                self._notify(
                    "invalidate-rejected",
                    key,
                    payload={
                        "revision": revision,
                        "current_revision": current_revision,
                    },
                )
                return
            next_revision = revision if revision is not None else current_revision + 1
            self._backend[key] = HistoryCacheEntry(
                history=_INVALID_HISTORY_SENTINEL,
                revision=next_revision,
            )
        self._notify("invalidate", key, payload={"revision": next_revision})

    def _notify(self, name: str, key: CacheKey, *, payload: Any = None) -> None:
        if not self._listeners:
            return
        event = HistoryCacheEvent(name=name, key=key, payload=payload)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:  # pragma: no cover - defensive guard
                logger.exception("History cache listener %r failed", listener)


class HistoryCacheSynchronizer:
    """Bridge repository events with the entry history cache."""

    __slots__ = ("_cache", "_events", "_entries")

    def __init__(
        self,
        *,
        event_bus: RepositoryEventBus | None,
        history_cache: HistoryCache | None,
        entries_repository: EntriesRepository | None,
    ) -> None:
        self._cache = history_cache
        self._events = event_bus
        self._entries = entries_repository
        if not self._events:
            return
        self._events.subscribe(
            ENTRY_HISTORY_CHANGED_EVENT, self._handle_history_changed
        )

    async def _handle_history_changed(
        self,
        *,
        user_id: str,
        created_date: str,
        reason: str,
        revision: int,
        entry_id: str | None = None,
        entry: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._cache:
            return
        if reason == "insert" and entry is not None:
            await self._cache.append(user_id, created_date, entry, revision=revision)
            return
        await self._cache.invalidate(user_id, created_date, revision=revision)


__all__ = [
    "HistoryCache",
    "HistoryCacheEvent",
    "HistoryCacheListener",
    "FrozenHistory",
    "HistoryCacheEntry",
    "HistoryCacheSynchronizer",
    "default_backend_factory",
]
