import os
import sqlite3
from contextlib import contextmanager
from threading import Lock


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
                """
            )

    @contextmanager
    def get_conn(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
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

    def get_session(self, session_id):
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]
