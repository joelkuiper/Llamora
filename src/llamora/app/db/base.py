from __future__ import annotations

from aiosqlitepool import SQLiteConnectionPool


async def run_in_transaction(conn, func, *args, **kwargs):
    """Execute the given coroutine within a transaction."""
    try:
        result = await func(*args, **kwargs)
        await conn.commit()
        return result
    except Exception:
        if conn.in_transaction:
            await conn.rollback()
        raise


class BaseRepository:
    """Common functionality shared by repository classes."""

    def __init__(self, pool: SQLiteConnectionPool):
        self.pool = pool

    async def _run_in_transaction(self, conn, func, *args, **kwargs):
        return await run_in_transaction(conn, func, *args, **kwargs)
