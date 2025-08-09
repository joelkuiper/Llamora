import os
import aiosqlite
import asyncio
from contextlib import asynccontextmanager
import secrets
from config import MAX_USERNAME_LENGTH
from ulid import ULID


class LocalDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("CHAT_DB_PATH", "state.sqlite3")
        is_new = not os.path.exists(self.db_path)
        asyncio.run(self._ensure_schema(is_new))

    async def _ensure_schema(self, is_new):
        async with self.get_conn() as conn:
            if is_new:
                print("Creating new database...")
            await conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL CHECK(length(username) <= {MAX_USERNAME_LENGTH}),
                    password_hash TEXT NOT NULL,
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
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id_id ON sessions(user_id, id);
                CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id_created ON sessions(user_id, ulid);
                """
            )

    @asynccontextmanager
    async def get_conn(self):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            yield conn
            await conn.commit()

    # user helpers
    async def create_user(self, username, password_hash):
        async with self.get_conn() as conn:
            ulid = str(ULID())
            await conn.execute(
                "INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                (ulid, username, password_hash),
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

    async def append(self, user_id, session_id, role, content):
        async with self.get_conn() as conn:
            ulid = str(ULID())
            if not await self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            await conn.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (ulid, session_id, role, content),
            )

            return ulid

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

    async def get_history(self, user_id, session_id):
        async with self.get_conn() as conn:
            if not await self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            cursor = await conn.execute(
                "SELECT id, role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

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

            if not current:
                return None

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

            return row["id"] if row else None
