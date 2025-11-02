from __future__ import annotations

import hashlib

from aiosqlitepool import SQLiteConnectionPool

from llamora.settings import settings
from .base import BaseRepository


class SearchHistoryRepository(BaseRepository):
    """Persist and retrieve encrypted user search history."""

    def __init__(
        self,
        pool: SQLiteConnectionPool,
        encrypt_message,
        decrypt_message,
    ) -> None:
        super().__init__(pool)
        self._encrypt_message = encrypt_message
        self._decrypt_message = decrypt_message

    async def record_search(self, user_id: str, query: str, dek: bytes) -> None:
        """Store or update a search query for the given user."""

        normalized = (query or "").strip()
        if not normalized:
            return

        query_hash = hashlib.sha256(
            f"{user_id}:{normalized.lower()}".encode("utf-8")
        ).digest()
        nonce, ct, alg = self._encrypt_message(
            dek, user_id, query_hash.hex(), normalized
        )

        async with self.pool.connection() as conn:

            async def _tx() -> None:
                await conn.execute(
                    """
                    INSERT INTO search_history (
                        user_id, query_hash, query_nonce, query_ct, alg, usage_count, last_used
                    ) VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, query_hash) DO UPDATE SET
                        query_nonce=excluded.query_nonce,
                        query_ct=excluded.query_ct,
                        alg=excluded.alg,
                        usage_count=usage_count + 1,
                        last_used=CURRENT_TIMESTAMP
                    """,
                    (user_id, query_hash, nonce, ct, alg.decode()),
                )
                await conn.execute(
                    """
                    DELETE FROM search_history
                    WHERE user_id = ?
                      AND query_hash NOT IN (
                        SELECT query_hash FROM search_history
                        WHERE user_id = ?
                        ORDER BY last_used DESC
                        LIMIT ?
                      )
                    """,
                    (user_id, user_id, int(settings.SEARCH.recent_limit)),
                )

            await self._run_in_transaction(conn, _tx)

    async def get_recent_searches(
        self, user_id: str, limit: int, dek: bytes
    ) -> list[str]:
        """Return the most recent search queries for the user."""

        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT query_hash, query_nonce, query_ct, alg
                FROM search_history
                WHERE user_id = ?
                ORDER BY last_used DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()

        results: list[str] = []
        for row in rows:
            try:
                decrypted = self._decrypt_message(
                    dek,
                    user_id,
                    row["query_hash"].hex(),
                    row["query_nonce"],
                    row["query_ct"],
                    row["alg"].encode(),
                )
            except Exception:
                continue

            cleaned = decrypted.strip()
            if cleaned and cleaned not in results:
                results.append(cleaned)

        return results[:limit]
