from __future__ import annotations

import logging
import time

from aiosqlitepool import SQLiteConnectionPool
from nacl.secret import SecretBox

from .base import BaseRepository

logger = logging.getLogger(__name__)


class SessionsRepository(BaseRepository):
    """Encrypted DEK session store backed by SQLite.

    Each row holds a DEK encrypted with the application's cookie secret.
    Sessions expire after *ttl* seconds with a sliding window: every
    successful load refreshes the deadline.
    """

    __slots__ = ("_box", "_ttl")

    def __init__(
        self,
        pool: SQLiteConnectionPool,
        box: SecretBox,
        ttl: int,
    ) -> None:
        super().__init__(pool)
        self._box = box
        self._ttl = ttl

    async def store(self, sid: str, dek: bytes) -> None:
        """Encrypt and persist a DEK, overwriting any existing row."""

        ciphertext = self._box.encrypt(dek)
        expires_at = int(time.time()) + self._ttl

        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO dek_sessions (sid, ciphertext, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(sid) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    expires_at = excluded.expires_at
                """,
                (sid, bytes(ciphertext), expires_at),
            )
            await conn.commit()

    async def load(self, sid: str) -> bytes | None:
        """Load and decrypt a DEK if the session has not expired.

        Refreshes the expiry on each access (sliding window).
        """

        now = int(time.time())
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT ciphertext FROM dek_sessions WHERE sid = ? AND expires_at > ?",
                (sid, now),
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            await conn.execute(
                "UPDATE dek_sessions SET expires_at = ? WHERE sid = ?",
                (now + self._ttl, sid),
            )
            await conn.commit()

        try:
            return self._box.decrypt(row["ciphertext"])
        except Exception:
            logger.warning("Failed to decrypt session %s; removing", sid[:8])
            await self.remove(sid)
            return None

    async def remove(self, sid: str) -> None:
        """Delete a specific session."""

        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM dek_sessions WHERE sid = ?",
                (sid,),
            )
            await conn.commit()

    async def purge_expired(self) -> int:
        """Delete all expired sessions.  Returns the count removed."""

        now = int(time.time())
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM dek_sessions WHERE expires_at <= ?",
                (now,),
            )
            await conn.commit()
            return cursor.rowcount or 0
