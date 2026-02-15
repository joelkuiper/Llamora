from __future__ import annotations

from aiosqlitepool import SQLiteConnectionPool


async def run_in_transaction(conn, func, *args, immediate=True, **kwargs):
    """Execute the given coroutine within a transaction.

    When *immediate* is True (the default), a ``BEGIN IMMEDIATE``
    statement is issued so the write lock is acquired upfront rather
    than on the first DML statement.  This prevents ``SQLITE_BUSY``
    errors that arise when a deferred transaction tries to upgrade to a
    write lock and another writer got there first.
    """
    if immediate and not conn.in_transaction:
        await conn.execute("BEGIN IMMEDIATE")
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
