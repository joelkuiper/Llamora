import os
import sqlite3
from contextlib import contextmanager
from threading import Lock
import secrets


class HistoryDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("CHAT_DB_PATH", "history.sqlite3")
        self._lock = Lock()
        is_new = not os.path.exists(self.db_path)
        self._ensure_schema(is_new)

    def _ensure_schema(self, is_new):
        with self.get_conn() as conn:
            if is_new:
                print("Creating new database...")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_messages_session_time ON messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);
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

    def append(self, session_id, role, content):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )

    def create_session(self):
        session_id = secrets.token_urlsafe(32)
        with self.get_conn() as conn:
            conn.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
        return session_id

    def delete_session(self, session_id):
        print(f"Deleting... {session_id}")
        with self.get_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def get_session(self, session_id):
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_adjacent_session(self, current_session_id, direction="next"):
        op = ">" if direction == "next" else "<"
        order = "ASC" if direction == "next" else "DESC"
        with self.get_conn() as conn:
            row = conn.execute(
                f"""
                SELECT id FROM sessions
                WHERE created_at {op} (SELECT created_at FROM sessions WHERE id = ?)
                ORDER BY created_at {order}
                LIMIT 1
                """,
                (current_session_id,),
            ).fetchone()
            return row["id"] if row else None
