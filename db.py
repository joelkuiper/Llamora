import os
import sqlite3
from contextlib import contextmanager
from threading import Lock
import secrets
from config import MAX_USERNAME_LENGTH
from ulid import ULID


class LocalDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("CHAT_DB_PATH", "state.sqlite3")
        self._lock = Lock()
        is_new = not os.path.exists(self.db_path)
        self._ensure_schema(is_new)

    def _ensure_schema(self, is_new):
        with self.get_conn() as conn:
            if is_new:
                print("Creating new database...")
            conn.executescript(
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
                    session_id TEXT UNIQUE NOT NULL,
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

    @contextmanager
    def get_conn(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")  # this is crucial
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # user helpers
    def create_user(self, username, password_hash):
        with self.get_conn() as conn:
            ulid = str(ULID())
            conn.execute(
                "INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                (ulid, username, password_hash),
            )

    def get_user_by_username(self, username):
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id):
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def _owns_session(self, conn, user_id, session_id):
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ? LIMIT 1",
            (session_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def get_session(self, user_id, session_id):
        with self.get_conn() as conn:
            return self._owns_session(conn, user_id, session_id)

    def rename_session(self, user_id, session_id, name):
        with self.get_conn() as conn:
            if not self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            conn.execute(
                "UPDATE sessions SET name = ? WHERE id = ? AND user_id = ?",
                (name, session_id, user_id),
            )

    def get_latest_session(self, user_id):
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE user_id = ? ORDER BY ulid DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return row["id"] if row else None

    def append(self, user_id, session_id, role, content):
        with self.get_conn() as conn:
            ulid = str(ULID())
            if not self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            conn.execute(
                "INSERT INTO messages (id, session_id, role, content) VALUES (?, ?, ?, ?)",
                (ulid, session_id, role, content),
            )

    def create_session(self, user_id):
        session_id = secrets.token_urlsafe(32)
        ulid = str(ULID())
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, ulid, user_id) VALUES (?, ?, ?)",
                (session_id, ulid, user_id),
            )
        return session_id

    def delete_session(self, user_id, session_id):
        with self.get_conn() as conn:
            if not self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            conn.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )

    def get_history(self, user_id, session_id):
        with self.get_conn() as conn:
            if not self._owns_session(conn, user_id, session_id):
                raise ValueError("User does not own session")

            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_all_sessions(self, user_id):
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at FROM sessions WHERE user_id = ? ORDER BY ulid DESC",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_adjacent_session(self, user_id, session_id, direction="next"):
        op = ">" if direction == "next" else "<"
        order = "ASC" if direction == "next" else "DESC"

        with self.get_conn() as conn:
            # Get the ULID of the current session
            current = conn.execute(
                "SELECT ulid FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()

            if not current:
                return None

            ulid = current["ulid"]

            # Find the adjacent session by ULID
            row = conn.execute(
                f"""
                SELECT id FROM sessions
                WHERE user_id = ? AND ulid {op} ?
                ORDER BY ulid {order}
                LIMIT 1
                """,
                (user_id, ulid),
            ).fetchone()

            return row["id"] if row else None
