from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from logging import getLogger
from types import MappingProxyType
from typing import Any, Protocol, cast

from cachetools import TTLCache

logger = getLogger(__name__)

CacheKey = tuple[str, str]


class HistoryCacheBackend(Protocol):
    def get(self, key: CacheKey, default: object | None = None) -> object | None: ...

    def __setitem__(self, key: CacheKey, value: object) -> None: ...


@dataclass(slots=True)
class HistoryCacheEvent:
    name: str
    key: CacheKey
    payload: Any = None


HistoryCacheListener = Callable[[HistoryCacheEvent], None]

FrozenHistory = tuple[Mapping[str, Any], ...]
_INVALID_HISTORY_SENTINEL = object()


def _freeze_history(history: Sequence[Mapping[str, Any] | dict[str, Any]]) -> FrozenHistory:
    frozen_messages: list[Mapping[str, Any]] = []
    for message in history:
        raw_message = dict(message)
        tags = raw_message.get("tags") or []
        raw_message["tags"] = tuple(MappingProxyType(dict(tag)) for tag in tags)
        raw_message["meta"] = MappingProxyType(dict(raw_message.get("meta") or {}))
        frozen_messages.append(MappingProxyType(raw_message))
    return tuple(frozen_messages)


def _thaw_history(history: FrozenHistory) -> list[dict[str, Any]]:
    thawed: list[dict[str, Any]] = []
    for message in history:
        hydrated = dict(message)
        hydrated["tags"] = [dict(tag) for tag in message.get("tags", ())]
        hydrated["meta"] = dict(message.get("meta", {}))
        thawed.append(hydrated)
    return thawed


def default_backend_factory(maxsize: int, ttl: int) -> TTLCache[CacheKey, object]:
    return TTLCache(maxsize=maxsize, ttl=ttl)


class HistoryCache:
    """Manage cached message history with observer hooks."""

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
        self, user_id: str, created_date: str, history: Sequence[Mapping[str, Any] | dict[str, Any]]
    ) -> None:
        key = (user_id, created_date)
        frozen = _freeze_history(history)
        async with self._lock:
            self._backend[key] = frozen
        self._notify("store", key, payload=_thaw_history(frozen))

    async def append(self, user_id: str, created_date: str, message: Mapping[str, Any]) -> None:
        key = (user_id, created_date)
        while True:
            async with self._lock:
                cached = self._backend.get(key)
            if cached is None or cached is _INVALID_HISTORY_SENTINEL:
                self._notify("append-skip", key)
                return
            frozen = cast(FrozenHistory, cached)
            history = _thaw_history(frozen)
            new_entry = dict(message)
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
                    self._notify("append", key, payload=dict(message))
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


__all__ = [
    "HistoryCache",
    "HistoryCacheEvent",
    "HistoryCacheListener",
    "FrozenHistory",
    "default_backend_factory",
]
