from __future__ import annotations

import logging
import time

from .base import BaseRepository

logger = logging.getLogger(__name__)


class TTLStore(BaseRepository):
    """Generic namespace-scoped key-value store with TTL expiry.

    Backed by a single ``ttl_store`` SQLite table shared across all
    namespaces.  Domain-specific repositories (sessions, login failures)
    wrap this with their own serialisation and semantics.
    """

    async def put(
        self,
        namespace: str,
        key: str,
        value: bytes,
        ttl: int,
    ) -> None:
        """Upsert a value with a fresh expiry deadline."""

        expires_at = int(time.time()) + ttl
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO ttl_store (namespace, key, value, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value      = excluded.value,
                    expires_at = excluded.expires_at
                """,
                (namespace, key, value, expires_at),
            )
            await conn.commit()

    async def get(
        self,
        namespace: str,
        key: str,
    ) -> bytes | None:
        """Return the stored value if it has not expired, else ``None``."""

        now = int(time.time())
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT value FROM ttl_store WHERE namespace = ? AND key = ? AND expires_at > ?",
                (namespace, key, now),
            )
            row = await cursor.fetchone()
        return bytes(row["value"]) if row else None

    async def get_and_refresh(
        self,
        namespace: str,
        key: str,
        ttl: int,
    ) -> bytes | None:
        """Return the value and extend its expiry (sliding window)."""

        now = int(time.time())
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT value FROM ttl_store WHERE namespace = ? AND key = ? AND expires_at > ?",
                (namespace, key, now),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await conn.execute(
                "UPDATE ttl_store SET expires_at = ? WHERE namespace = ? AND key = ?",
                (now + ttl, namespace, key),
            )
            await conn.commit()
        return bytes(row["value"])

    async def remove(self, namespace: str, key: str) -> None:
        """Delete a specific entry."""

        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM ttl_store WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            await conn.commit()

    async def remove_namespace(self, namespace: str) -> int:
        """Delete all entries in a namespace and return the number removed."""

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM ttl_store WHERE namespace = ?",
                (namespace,),
            )
            await conn.commit()
            return cursor.rowcount or 0

    async def increment(
        self,
        namespace: str,
        key: str,
        ttl: int,
    ) -> int:
        """Atomically increment an integer counter.

        The counter is stored as a UTF-8 digit string inside the BLOB
        column.  If the key has expired or does not exist, the counter
        resets to 1.  Returns the new count.
        """

        one = b"1"
        now = int(time.time())
        expires_at = now + ttl
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO ttl_store (namespace, key, value, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value = CASE
                        WHEN ttl_store.expires_at <= ?
                            THEN ?
                        ELSE CAST(CAST(ttl_store.value AS INTEGER) + 1 AS TEXT)
                    END,
                    expires_at = excluded.expires_at
                """,
                (namespace, key, one, expires_at, now, one),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT CAST(value AS INTEGER) AS count FROM ttl_store WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            row = await cursor.fetchone()
        return int(row["count"]) if row else 1

    async def get_int(self, namespace: str, key: str) -> int:
        """Return an integer counter value, or 0 if expired/absent."""

        raw = await self.get(namespace, key)
        if raw is None:
            return 0
        try:
            return int(raw)
        except (ValueError, TypeError):
            return 0

    async def purge_expired(self) -> int:
        """Delete all expired entries across all namespaces."""

        now = int(time.time())
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM ttl_store WHERE expires_at <= ?",
                (now,),
            )
            await conn.commit()
            return cursor.rowcount or 0
