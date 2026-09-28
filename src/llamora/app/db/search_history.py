from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from aiosqlitepool import SQLiteConnectionPool

from llamora.settings import settings
from .base import BaseRepository
from llamora.app.services.crypto import CryptoContext


class SearchHistoryRepository(BaseRepository):
    """Persist and retrieve encrypted user search history."""

    def __init__(self, pool: SQLiteConnectionPool) -> None:
        super().__init__(pool)

    async def record_search(self, ctx: CryptoContext, query: str) -> None:
        """Store or update a search query for the given user."""

        normalized = (query or "").strip()
        if not normalized:
            return
        ctx.require_write(operation="search_history.record_search")

        query_hash = hashlib.sha256(
            f"{ctx.user_id}:{normalized.lower()}".encode("utf-8")
        ).digest()
        nonce, ct, alg = ctx.encrypt_entry(query_hash.hex(), normalized)

        async with self.pool.connection() as conn:

            async def _tx() -> None:
                stamp = await _next_stamp(conn, ctx.user_id)
                await conn.execute(
                    """
                    INSERT INTO search_history (
                        user_id, query_hash, query_nonce, query_ct, alg, usage_count, last_used
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(user_id, query_hash) DO UPDATE SET
                        query_nonce=excluded.query_nonce,
                        query_ct=excluded.query_ct,
                        alg=excluded.alg,
                        usage_count=usage_count + 1,
                        last_used=excluded.last_used
                    """,
                    (ctx.user_id, query_hash, nonce, ct, alg.decode(), stamp),
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
                    (ctx.user_id, ctx.user_id, int(settings.SEARCH.recent_limit)),
                )

            await self._run_in_transaction(conn, _tx)

    async def get_recent_searches(self, ctx: CryptoContext, limit: int) -> list[str]:
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
                (ctx.user_id, limit),
            )
            rows = await cursor.fetchall()

        results: list[str] = []
        for row in rows:
            try:
                decrypted = ctx.decrypt_entry(
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


async def _next_stamp(conn, user_id: str) -> str:
    """Now, as a sortable UTC timestamp, later than any the user already has.

    CURRENT_TIMESTAMP has whole seconds, so searches refined within a second
    tied: recent searches came back in any order, and trimming to the limit
    could drop the newest. Microseconds, kept strictly increasing per user,
    order them by use. (Older rows without fractions still sort correctly.)
    """
    cursor = await conn.execute(
        "SELECT MAX(last_used) AS latest FROM search_history WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    latest = row["latest"] if row else None
    if latest:
        previous = datetime.fromisoformat(str(latest))
        if now <= previous:
            now = previous + timedelta(microseconds=1)
    return now.isoformat(sep=" ", timespec="microseconds")
