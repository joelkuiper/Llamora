from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from logging import getLogger
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from cachetools import TTLCache

from llamora.app.db.events import (
    ENTRY_HISTORY_CHANGED_EVENT,
    ENTRY_TAGS_CHANGED_EVENT,
)

logger = getLogger(__name__)

CacheKey = tuple[str, str]

if TYPE_CHECKING:
    from llamora.app.db.events import RepositoryEventBus
    from llamora.app.db.entries import EntriesRepository


HistoryCacheBackend = MutableMapping[CacheKey, object]


@dataclass(slots=True)
class HistoryCacheEvent:
    name: str
    key: CacheKey
    payload: Any = None


HistoryCacheListener = Callable[[HistoryCacheEvent], None]

FrozenHistory = tuple[Mapping[str, Any], ...]
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
        if cached is None or cached is _INVALID_HISTORY_SENTINEL:
            self._notify("miss", key)
            return None
        frozen = cast(FrozenHistory, cached)
        history = _thaw_history(frozen)
        self._notify("hit", key, payload=history)
        return history

    async def store(
        self,
        user_id: str,
        created_date: str,
        history: Sequence[Mapping[str, Any] | dict[str, Any]],
    ) -> None:
        key = (user_id, created_date)
        frozen = _freeze_history(history)
        async with self._lock:
            self._backend[key] = frozen
        self._notify("store", key, payload=_thaw_history(frozen))

    async def append(
        self, user_id: str, created_date: str, entry: Mapping[str, Any]
    ) -> None:
        key = (user_id, created_date)
        while True:
            async with self._lock:
                cached = self._backend.get(key)
            if cached is None or cached is _INVALID_HISTORY_SENTINEL:
                self._notify("append-skip", key)
                return
            frozen = cast(FrozenHistory, cached)
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
                    self._backend[key] = updated
                    self._notify("append", key, payload=dict(entry))
                    return

    async def invalidate(self, user_id: str, created_date: str) -> None:
        key = (user_id, created_date)
        async with self._lock:
            self._backend[key] = _INVALID_HISTORY_SENTINEL
        self._notify("invalidate", key)

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
        self._events.subscribe(ENTRY_TAGS_CHANGED_EVENT, self._handle_tags_changed)

    async def _handle_history_changed(
        self,
        *,
        user_id: str,
        created_date: str,
        reason: str,
        entry_id: str | None = None,
        entry: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._cache:
            return
        if reason == "insert" and entry is not None:
            await self._cache.append(user_id, created_date, entry)
            return
        await self._cache.invalidate(user_id, created_date)

    async def _handle_tags_changed(
        self,
        *,
        user_id: str,
        entry_id: str,
        tag_hash: bytes | str | None = None,
    ) -> None:
        if not self._events or not self._entries:
            return
        created_date = await self._entries.get_entry_date(user_id, entry_id)
        if not created_date:
            return
        await self._events.emit_for_entry_date(
            ENTRY_HISTORY_CHANGED_EVENT,
            user_id=user_id,
            created_date=created_date,
            entry_id=entry_id,
            reason="tags-changed",
        )


__all__ = [
    "HistoryCache",
    "HistoryCacheEvent",
    "HistoryCacheListener",
    "FrozenHistory",
    "HistoryCacheSynchronizer",
    "default_backend_factory",
]
