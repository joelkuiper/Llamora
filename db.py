import os
import aiosqlite
from contextlib import asynccontextmanager
import secrets
import logging
import json
import asyncio
from config import (
    MAX_USERNAME_LENGTH,
    DB_POOL_SIZE,
    DB_POOL_ACQUIRE_TIMEOUT,
    DB_TIMEOUT,
    DB_BUSY_TIMEOUT,
    DB_MMAP_SIZE,
)
from ulid import ULID
from aiosqlitepool import SQLiteConnectionPool


class LocalDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("LLAMORA_DB_PATH", "state.sqlite3")
        self.pool = None
        self.search_api = None

    def set_search_api(self, api):
        self.search_api = api

    async def init(self):
        is_new = not os.path.exists(self.db_path)
        self.pool = SQLiteConnectionPool(
            self._connection_factory,
            pool_size=DB_POOL_SIZE,
            acquisition_timeout=DB_POOL_ACQUIRE_TIMEOUT,
        )
        await self._ensure_schema(is_new)

    async def close(self):
        if self.pool is not None:
            await self.pool.close()

    async def _connection_factory(self):
        conn = await aiosqlite.connect(self.db_path, timeout=DB_TIMEOUT)
        await conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT}")
        await conn.execute(f"PRAGMA mmap_size = {DB_MMAP_SIZE}")
        await conn.execute("PRAGMA foreign_keys = ON")
        # From https://github.com/slaily/aiosqlitepool
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.execute("PRAGMA cache_size = 10000")
        await conn.execute("PRAGMA temp_store = MEMORY")
        conn.row_factory = aiosqlite.Row
        return conn

    async def _ensure_schema(self, is_new):
        if is_new:
            logging.getLogger(__name__).info(
                "Creating new database at %s", self.db_path
            )
        async with self.get_conn() as conn:
            await conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL CHECK(length(username) <= {MAX_USERNAME_LENGTH}),
                    password_hash TEXT NOT NULL,
                    dek_pw_salt BLOB NOT NULL,
                    dek_pw_nonce BLOB NOT NULL,
                    dek_pw_cipher BLOB NOT NULL,
                    dek_rc_salt BLOB NOT NULL,
                    dek_rc_nonce BLOB NOT NULL,
                    dek_rc_cipher BLOB NOT NULL,
                    state TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    ulid TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL,
                    name TEXT DEFAULT 'Untitled',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    alg BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    alg BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (id) REFERENCES messages(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id_id ON sessions(user_id, id);
                CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id_created ON sessions(user_id, ulid);
                CREATE INDEX IF NOT EXISTS idx_vectors_user_id ON vectors(user_id);
                CREATE INDEX IF NOT EXISTS idx_vectors_id ON vectors(id);
                """
            )

    @asynccontextmanager
    async def get_conn(self):
        if self.pool is None:
            raise RuntimeError("Database has not been initialized")
        async with self.pool.connection() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    # user helpers
    async def create_user(
        self,
        username,
        password_hash,
        pw_salt,
        pw_nonce,
        pw_cipher,
        rc_salt,
        rc_nonce,
        rc_cipher,
    ):
        ulid = str(ULID())
        async with self.get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO users (
                    id, username, password_hash,
                    dek_pw_salt, dek_pw_nonce, dek_pw_cipher,
                    dek_rc_salt, dek_rc_nonce, dek_rc_cipher
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ulid,
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

    async def get_user_by_username(self, username):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, user_id):
        async with self.get_conn() as conn:
            cursor = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_state(self, user_id):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                "SELECT state FROM users WHERE id = ?", (user_id,)
            )
            row = await cursor.fetchone()
        if row and row["state"]:
            try:
                return json.loads(row["state"])
            except Exception:
                return {}
        return {}

    async def update_state(self, user_id, **updates):
        state = await self.get_state(user_id)
        for key, value in updates.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        state_json = json.dumps(state)
        async with self.get_conn() as conn:
            await conn.execute(
                "UPDATE users SET state = ? WHERE id = ?",
                (state_json, user_id),
            )

    async def _owns_session(self, conn, user_id, session_id):
        cursor = await conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ? LIMIT 1",
            (session_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_session(self, user_id, session_id):
        async with self.get_conn() as conn:
            return await self._owns_session(conn, user_id, session_id)

    async def rename_session(self, user_id, session_id, name):
        async with self.get_conn() as conn:
            if not await self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            await conn.execute(
                "UPDATE sessions SET name = ? WHERE id = ? AND user_id = ?",
                (name, session_id, user_id),
            )

    async def get_latest_session(self, user_id):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                "SELECT id FROM sessions WHERE user_id = ? ORDER BY ulid DESC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
        return row["id"] if row else None

    async def update_password_wrap(
        self, user_id, password_hash, pw_salt, pw_nonce, pw_cipher
    ):
        async with self.get_conn() as conn:
            await conn.execute(
                "UPDATE users SET password_hash = ?, dek_pw_salt = ?, dek_pw_nonce = ?, dek_pw_cipher = ? WHERE id = ?",
                (password_hash, pw_salt, pw_nonce, pw_cipher, user_id),
            )

    async def update_recovery_wrap(self, user_id, rc_salt, rc_nonce, rc_cipher):
        async with self.get_conn() as conn:
            await conn.execute(
                "UPDATE users SET dek_rc_salt = ?, dek_rc_nonce = ?, dek_rc_cipher = ? WHERE id = ?",
                (rc_salt, rc_nonce, rc_cipher, user_id),
            )

    async def delete_user(self, user_id):
        async with self.get_conn() as conn:
            await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    async def append(self, user_id, session_id, role, content, dek):
        from app.services.crypto import encrypt_message

        async with self.get_conn() as conn:
            if not await self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

        ulid = str(ULID())
        nonce, ct, alg = encrypt_message(dek, user_id, session_id, ulid, content)

        async with self.get_conn() as conn:
            await conn.execute(
                "INSERT INTO messages (id, session_id, role, nonce, ciphertext, alg) VALUES (?, ?, ?, ?, ?, ?)",
                (ulid, session_id, role, nonce, ct, alg),
            )
        msg_id = ulid

        if self.search_api:
            asyncio.create_task(
                self.search_api.on_message_appended(
                    user_id, session_id, msg_id, content, dek
                )
            )

        return msg_id

    async def store_vector(self, msg_id, user_id, dim, nonce, ciphertext, alg):
        async with self.get_conn() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO vectors (id, user_id, dim, nonce, ciphertext, alg)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (msg_id, user_id, dim, nonce, ciphertext, alg),
            )

    async def get_latest_vectors(self, user_id: str, limit: int):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                """
                SELECT v.id, v.dim, v.nonce, v.ciphertext, v.alg, m.session_id, m.created_at
                FROM vectors v
                JOIN messages m ON v.id = m.id
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_latest_messages(self, user_id: str, limit: int):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.session_id, m.role, m.nonce, m.ciphertext, m.alg, m.created_at
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_vectors_older_than(self, user_id: str, before_id: str, limit: int):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                """
                SELECT v.id, v.dim, v.nonce, v.ciphertext, v.alg, m.session_id, m.created_at
                FROM vectors v
                JOIN messages m ON v.id = m.id
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ? AND m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, before_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_messages_older_than(self, user_id: str, before_id: str, limit: int):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.session_id, m.role, m.nonce, m.ciphertext, m.alg, m.created_at
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ? AND m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, before_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_latest_id(self, user_id: str):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ?
                ORDER BY m.id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
        return row["id"] if row else None

    async def get_messages_by_ids(self, user_id: str, ids: list[str]):
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                f"""
                SELECT m.id, m.session_id, m.role, m.nonce, m.ciphertext, m.alg
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE s.user_id = ? AND m.id IN ({placeholders})
                """,
                (user_id, *ids),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def create_session(self, user_id):
        session_id = secrets.token_urlsafe(32)
        ulid = str(ULID())
        async with self.get_conn() as conn:
            await conn.execute(
                "INSERT INTO sessions (id, ulid, user_id) VALUES (?, ?, ?)",
                (session_id, ulid, user_id),
            )
        return session_id

    async def delete_session(self, user_id, session_id):
        async with self.get_conn() as conn:
            if not await self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            await conn.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )

    async def get_history(self, user_id, session_id, dek):
        from app.services.crypto import decrypt_message

        async with self.get_conn() as conn:
            if not await self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            cursor = await conn.execute(
                "SELECT id, role, nonce, ciphertext, alg FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()

        history = []
        for row in rows:
            content = decrypt_message(
                dek,
                user_id,
                session_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            history.append({"id": row["id"], "role": row["role"], "content": content})
        return history

    async def get_all_sessions(self, user_id):
        async with self.get_conn() as conn:
            cursor = await conn.execute(
                "SELECT id, name, created_at FROM sessions WHERE user_id = ? ORDER BY ulid DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_adjacent_session(self, user_id, session_id, direction="next"):
        op = ">" if direction == "next" else "<"
        order = "ASC" if direction == "next" else "DESC"
        async with self.get_conn() as conn:
            current_cursor = await conn.execute(
                "SELECT ulid FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
            current = await current_cursor.fetchone()

            if current:
                ulid = current["ulid"]
                cursor = await conn.execute(
                    f"""
                    SELECT id FROM sessions
                    WHERE user_id = ? AND ulid {op} ?
                    ORDER BY ulid {order}
                    LIMIT 1
                    """,
                    (user_id, ulid),
                )
                row = await cursor.fetchone()
            else:
                row = None
        if not current:
            return None
        return row["id"] if row else None
