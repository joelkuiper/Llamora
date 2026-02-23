from __future__ import annotations

from .ttl_store import TTLStore

NAMESPACE = "login_failures"


class LoginFailuresRepository:
    """Login failure counter for rate limiting.

    Thin wrapper over :class:`TTLStore` that provides atomic
    increment/read/clear semantics for per-key failure counts.
    """

    __slots__ = ("_store", "_ttl")

    def __init__(self, store: TTLStore, ttl: int) -> None:
        self._store = store
        self._ttl = ttl

    async def get_attempts(self, cache_key: str) -> int:
        """Return the current failure count, or 0 if expired/absent."""

        return await self._store.get_int(NAMESPACE, cache_key)

    async def record_failure(self, cache_key: str) -> int:
        """Atomically increment and return the failure count."""

        return await self._store.increment(NAMESPACE, cache_key, self._ttl)

    async def clear(self, cache_key: str) -> None:
        """Remove failure tracking for a key (e.g. after successful login)."""

        await self._store.remove(NAMESPACE, cache_key)
