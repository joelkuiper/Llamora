from __future__ import annotations

import orjson
from aiosqlitepool import SQLiteConnectionPool
from ulid import ULID

from .base import BaseRepository


class UsersRepository(BaseRepository):
    """Data access helpers for the users table."""

    def __init__(self, pool: SQLiteConnectionPool):
        super().__init__(pool)

    async def create_user(
        self,
        username: str,
        password_hash: str,
        pw_salt: bytes,
        pw_nonce: bytes,
        pw_cipher: bytes,
        rc_salt: bytes,
        rc_nonce: bytes,
        rc_cipher: bytes,
    ) -> str:
        user_id = str(ULID())
        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.execute,
                """
                    INSERT INTO users (
                        id, username, password_hash,
                        dek_pw_salt, dek_pw_nonce, dek_pw_cipher,
                        dek_rc_salt, dek_rc_nonce, dek_rc_cipher
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    user_id,
                    username,
                    password_hash,
                    pw_salt,
                    pw_nonce,
                    pw_cipher,
                    rc_salt,
                    rc_nonce,
                    rc_cipher,
                ),
            )
        return user_id

    async def get_user_by_username(self, username: str) -> dict | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, user_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def users_table_empty(self) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute("SELECT 1 FROM users LIMIT 1")
            row = await cursor.fetchone()
        return row is None

    async def get_state(self, user_id: str) -> dict:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT state FROM users WHERE id = ?", (user_id,)
            )
            row = await cursor.fetchone()
        if row and row["state"]:
            try:
                return orjson.loads(row["state"])
            except Exception:
                return {}
        return {}

    async def update_state(self, user_id: str, **updates) -> None:
        async with self.pool.connection() as conn:

            async def _update() -> None:
                cursor = await conn.execute(
                    "SELECT state FROM users WHERE id = ?", (user_id,)
                )
                row = await cursor.fetchone()

                state: dict = {}
                if row and row["state"]:
                    try:
                        state = orjson.loads(row["state"])
                    except Exception:
                        state = {}

                for key, value in updates.items():
                    if value is None:
                        state.pop(key, None)
                    else:
                        state[key] = value

                state_json = orjson.dumps(state)
                await conn.execute(
                    "UPDATE users SET state = ? WHERE id = ?",
                    (state_json, user_id),
                )

            await self._run_in_transaction(conn, _update)

    async def update_password_wrap(
        self,
        user_id: str,
        password_hash: str,
        pw_salt: bytes,
        pw_nonce: bytes,
        pw_cipher: bytes,
    ) -> None:
        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.execute,
                "UPDATE users SET password_hash = ?, dek_pw_salt = ?, dek_pw_nonce = ?, dek_pw_cipher = ? WHERE id = ?",
                (password_hash, pw_salt, pw_nonce, pw_cipher, user_id),
            )

    async def update_recovery_wrap(
        self, user_id: str, rc_salt: bytes, rc_nonce: bytes, rc_cipher: bytes
    ) -> None:
        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.execute,
                "UPDATE users SET dek_rc_salt = ?, dek_rc_nonce = ?, dek_rc_cipher = ? WHERE id = ?",
                (rc_salt, rc_nonce, rc_cipher, user_id),
            )

    async def delete_user(self, user_id: str) -> None:
        async with self.pool.connection() as conn:
            await self._run_in_transaction(
                conn,
                conn.execute,
                "DELETE FROM users WHERE id = ?",
                (user_id,),
            )
