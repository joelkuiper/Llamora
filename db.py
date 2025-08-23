import os
import aiosqlite
import secrets
import logging
import orjson
import asyncio
import numpy as np
import hashlib
import atexit
from functools import lru_cache
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
from app.services.crypto import (
    encrypt_message,
    decrypt_message,
    encrypt_vector,
    decrypt_vector,
)


@lru_cache(maxsize=2048)
def _cached_tag_name(
    user_id: str,
    tag_hash: bytes,
    name_nonce: bytes,
    name_ct: bytes,
    alg: bytes,
    dek: bytes,
) -> str:
    return decrypt_message(
        dek, user_id, tag_hash.hex(), name_nonce, name_ct, alg
    )


class LocalDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or os.getenv("LLAMORA_DB_PATH", "state.sqlite3")
        self.pool = None
        self.search_api = None
        atexit.register(self._atexit_close)

    def _atexit_close(self):
        if self.pool is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.pool.close())
                else:
                    loop.run_until_complete(self.pool.close())
            except Exception:
                pass

    def __del__(self):
        self._atexit_close()

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

    async def with_transaction(self, conn, func, *args, **kwargs):
        try:
            result = await func(*args, **kwargs)
            await conn.commit()
            return result
        except Exception:
            if conn.in_transaction:
                await conn.rollback()
            raise

    async def _ensure_schema(self, is_new):
        if is_new:
            logging.getLogger(__name__).info(
                "Creating new database at %s", self.db_path
            )
        async with self.pool.connection() as conn:
            await self.with_transaction(
                conn,
                conn.executescript,
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

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    reply_to TEXT,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    alg BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_date TEXT DEFAULT (date('now')),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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

                CREATE TABLE IF NOT EXISTS tags (
                    user_id TEXT NOT NULL,
                    tag_hash BLOB(32) NOT NULL,
                    name_ct BLOB NOT NULL,
                    name_nonce BLOB(24) NOT NULL,
                    alg TEXT NOT NULL,
                    seen INTEGER DEFAULT 0,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, tag_hash)
                );

                CREATE TABLE IF NOT EXISTS tag_message_xref (
                    user_id TEXT NOT NULL,
                    tag_hash BLOB(32) NOT NULL,
                    message_id TEXT NOT NULL,
                    ulid TEXT NOT NULL,
                    PRIMARY KEY(user_id, tag_hash, message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_user_date ON messages(user_id, created_date);
                CREATE INDEX IF NOT EXISTS idx_messages_reply_to ON messages(reply_to);
                CREATE INDEX IF NOT EXISTS idx_vectors_user_id ON vectors(user_id);
                CREATE INDEX IF NOT EXISTS idx_vectors_id ON vectors(id);

                CREATE INDEX IF NOT EXISTS idx_tag_message_hash ON tag_message_xref(user_id, tag_hash);
                CREATE INDEX IF NOT EXISTS idx_tag_message_message ON tag_message_xref(user_id, message_id);
                """,
            )

            await conn.commit()

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
        async with self.pool.connection() as conn:
            await self.with_transaction(
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
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_by_id(self, user_id):
        async with self.pool.connection() as conn:
            cursor = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def users_table_empty(self) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute("SELECT 1 FROM users LIMIT 1")
            row = await cursor.fetchone()
        return row is None

    async def get_state(self, user_id):
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

    async def update_state(self, user_id, **updates):
        state = await self.get_state(user_id)
        for key, value in updates.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        state_json = orjson.dumps(state)
        async with self.pool.connection() as conn:
            await self.with_transaction(
                conn,
                conn.execute,
                "UPDATE users SET state = ? WHERE id = ?",
                (state_json, user_id),
            )

    async def update_password_wrap(
        self, user_id, password_hash, pw_salt, pw_nonce, pw_cipher
    ):
        async with self.pool.connection() as conn:
            await self.with_transaction(
                conn,
                conn.execute,
                "UPDATE users SET password_hash = ?, dek_pw_salt = ?, dek_pw_nonce = ?, dek_pw_cipher = ? WHERE id = ?",
                (password_hash, pw_salt, pw_nonce, pw_cipher, user_id),
            )

    async def update_recovery_wrap(self, user_id, rc_salt, rc_nonce, rc_cipher):
        async with self.pool.connection() as conn:
            await self.with_transaction(
                conn,
                conn.execute,
                "UPDATE users SET dek_rc_salt = ?, dek_rc_nonce = ?, dek_rc_cipher = ? WHERE id = ?",
                (rc_salt, rc_nonce, rc_cipher, user_id),
            )

    async def delete_user(self, user_id):
        async with self.pool.connection() as conn:
            await self.with_transaction(
                conn,
                conn.execute,
                "DELETE FROM users WHERE id = ?",
                (user_id,),
            )

    async def append_message(
        self,
        user_id,
        role,
        message,
        dek,
        meta=None,
        reply_to: str | None = None,
        created_date: str | None = None,
    ):
        ulid = str(ULID())
        record = {"message": message, "meta": meta or {}}
        plaintext = orjson.dumps(record).decode()
        nonce, ct, alg = encrypt_message(dek, user_id, ulid, plaintext)

        async with self.pool.connection() as conn:
            if created_date:
                await self.with_transaction(
                    conn,
                    conn.execute,
                    "INSERT INTO messages (id, user_id, role, reply_to, nonce, ciphertext, alg, created_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ulid, user_id, role, reply_to, nonce, ct, alg, created_date),
                )
            else:
                await self.with_transaction(
                    conn,
                    conn.execute,
                    "INSERT INTO messages (id, user_id, role, reply_to, nonce, ciphertext, alg) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ulid, user_id, role, reply_to, nonce, ct, alg),
                )

        msg_id = ulid

        if self.search_api:
            asyncio.create_task(
                self.search_api.on_message_appended(
                    user_id, msg_id, message, dek
                )
            )

        return msg_id

    async def resolve_or_create_tag(
        self, user_id: str, tag_name: str, dek: bytes
    ) -> bytes:
        tag_name = tag_name.strip()[:64]
        if not tag_name:
            raise ValueError("Empty tag")
        tag_hash = hashlib.sha256(f"{user_id}:{tag_name}".encode("utf-8")).digest()
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM tags WHERE user_id = ? AND tag_hash = ?",
                (user_id, tag_hash),
            )
            row = await cursor.fetchone()
            if not row:
                nonce, ct, alg = encrypt_message(
                    dek, user_id, tag_hash.hex(), tag_name
                )
                await self.with_transaction(
                    conn,
                    conn.execute,
                    "INSERT INTO tags (user_id, tag_hash, name_ct, name_nonce, alg) VALUES (?, ?, ?, ?, ?)",
                    (user_id, tag_hash, ct, nonce, alg.decode()),
                )
        return tag_hash

    async def xref_tag_message(
        self, user_id: str, tag_hash: bytes, message_id: str
    ) -> None:
        async with self.pool.connection() as conn:

            async def _tx():
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO tag_message_xref (user_id, tag_hash, message_id, ulid) VALUES (?, ?, ?, ?)",
                    (user_id, tag_hash, message_id, str(ULID())),
                )
                if cursor.rowcount:
                    await conn.execute(
                        "UPDATE tags SET seen = seen + 1, last_seen = CURRENT_TIMESTAMP WHERE user_id = ? AND tag_hash = ?",
                        (user_id, tag_hash),
                    )

            await self.with_transaction(conn, _tx)

    async def unlink_tag_message(
        self, user_id: str, tag_hash: bytes, message_id: str
    ) -> None:
        async with self.pool.connection() as conn:

            async def _tx():
                cursor = await conn.execute(
                    "DELETE FROM tag_message_xref WHERE user_id = ? AND tag_hash = ? AND message_id = ?",
                    (user_id, tag_hash, message_id),
                )
                if cursor.rowcount:
                    await conn.execute(
                        "UPDATE tags SET seen = CASE WHEN seen > 0 THEN seen - 1 ELSE 0 END WHERE user_id = ? AND tag_hash = ?",
                        (user_id, tag_hash),
                    )

            await self.with_transaction(conn, _tx)

    async def message_exists(self, user_id: str, message_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM messages WHERE id = ? AND user_id = ?",
                (message_id, user_id),
            )
            row = await cursor.fetchone()
        return bool(row)

    async def get_message_date(
        self, user_id: str, message_id: str
    ) -> str | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT created_date FROM messages WHERE id = ? AND user_id = ?",
                (message_id, user_id),
            )
            row = await cursor.fetchone()
        return row["created_date"] if row else None

    async def get_tags_for_message(self, user_id: str, message_id: str, dek: bytes):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT t.tag_hash, t.name_ct, t.name_nonce, t.alg AS tag_alg
                FROM tag_message_xref x
                JOIN tags t ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                WHERE x.user_id = ? AND x.message_id = ?
                ORDER BY x.ulid ASC
                """,
                (user_id, message_id),
            )
            rows = await cursor.fetchall()

        tags = []
        for row in rows:
            tag_name = _cached_tag_name(
                user_id,
                row["tag_hash"],
                row["name_nonce"],
                row["name_ct"],
                row["tag_alg"].encode(),
                dek,
            )
            tags.append({"name": tag_name, "hash": row["tag_hash"].hex()})
        return tags

    async def get_tag_frecency(
        self, user_id: str, limit: int, lambda_: float, dek: bytes
    ) -> list[dict]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT tag_hash, name_ct, name_nonce, alg,
                       seen AS frequency,
                       (julianday('now') - julianday(last_seen)) * 86400 AS recency,
                       seen / exp(? * (julianday('now') - julianday(last_seen)) * 86400) AS frecency
                FROM tags
                WHERE user_id = ?
                ORDER BY frecency DESC
                LIMIT ?
                """,
                (lambda_, user_id, limit),
            )
            rows = await cursor.fetchall()

        tags = []
        for row in rows:
            tag_name = _cached_tag_name(
                user_id,
                row["tag_hash"],
                row["name_nonce"],
                row["name_ct"],
                row["alg"].encode(),
                dek,
            )
            tags.append(
                {
                    "name": tag_name,
                    "hash": row["tag_hash"].hex(),
                    "frequency": row["frequency"],
                    "recency": row["recency"],
                    "frecency": row["frecency"],
                }
            )
        return tags

    async def get_messages_with_tag_hashes(
        self, user_id: str, tag_hashes: list[bytes], message_ids: list[str]
    ) -> dict[str, set[bytes]]:
        if not tag_hashes or not message_ids:
            return {}
        tag_placeholders = ",".join("?" * len(tag_hashes))
        msg_placeholders = ",".join("?" * len(message_ids))
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT message_id, tag_hash FROM tag_message_xref
                WHERE user_id = ? AND tag_hash IN ({tag_placeholders}) AND message_id IN ({msg_placeholders})
                """,
                (user_id, *tag_hashes, *message_ids),
            )
            rows = await cursor.fetchall()
        mapping: dict[str, set[bytes]] = {}
        for row in rows:
            mapping.setdefault(row["message_id"], set()).add(row["tag_hash"])
        return mapping

    async def store_vector(self, msg_id, user_id, vec, dek):
        vec_arr = np.asarray(vec, dtype=np.float32)
        dim = vec_arr.shape[0]
        nonce, ct, alg = encrypt_vector(
            dek, user_id, msg_id, vec_arr.tobytes()
        )
        async with self.pool.connection() as conn:
            await self.with_transaction(
                conn,
                conn.execute,
                """
                    INSERT OR REPLACE INTO vectors (id, user_id, dim, nonce, ciphertext, alg)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                (msg_id, user_id, dim, nonce, ct, alg),
            )

    async def get_latest_vectors(self, user_id: str, limit: int, dek: bytes):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT v.id, v.dim, v.nonce, v.ciphertext, v.alg, m.created_at
                FROM vectors v
                JOIN messages m ON v.id = m.id AND m.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()

        vectors = []
        for row in rows:
            vec_bytes = decrypt_vector(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            vec = np.frombuffer(vec_bytes, dtype=np.float32).reshape(row["dim"])
            vectors.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "vec": vec,
                }
            )
        return vectors

    async def get_latest_messages(self, user_id: str, limit: int, dek: bytes):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.nonce, m.ciphertext, m.alg, m.created_at
                FROM messages m
                WHERE m.user_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            record_json = decrypt_message(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            rec = orjson.loads(record_json)
            messages.append(
                {
                    "id": row["id"],
                    "role": row["role"],
                    "created_at": row["created_at"],
                    "message": rec.get("message", ""),
                    "meta": rec.get("meta", {}),
                }
            )
        return messages

    async def get_vectors_older_than(
        self, user_id: str, before_id: str, limit: int, dek: bytes
    ):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT v.id, v.dim, v.nonce, v.ciphertext, v.alg, m.created_at
                FROM vectors v
                JOIN messages m ON v.id = m.id AND m.user_id = ?
                WHERE m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, before_id, limit),
            )
            rows = await cursor.fetchall()

        vectors = []
        for row in rows:
            vec_bytes = decrypt_vector(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            vec = np.frombuffer(vec_bytes, dtype=np.float32).reshape(row["dim"])
            vectors.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "vec": vec,
                }
            )
        return vectors

    async def get_messages_older_than(
        self, user_id: str, before_id: str, limit: int, dek: bytes
    ):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.nonce, m.ciphertext, m.alg, m.created_at
                FROM messages m
                WHERE m.user_id = ? AND m.id < ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (user_id, before_id, limit),
            )
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            record_json = decrypt_message(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            rec = orjson.loads(record_json)
            messages.append(
                {
                    "id": row["id"],
                    "role": row["role"],
                    "created_at": row["created_at"],
                    "message": rec.get("message", ""),
                    "meta": rec.get("meta", {}),
                }
            )
        return messages

    async def get_user_latest_id(self, user_id: str):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id
                FROM messages m
                WHERE m.user_id = ?
                ORDER BY m.id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()
        return row["id"] if row else None

    async def get_messages_by_ids(self, user_id: str, ids: list[str], dek: bytes):
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                f"""
                SELECT m.id, m.created_at, m.role, m.nonce, m.ciphertext, m.alg
                FROM messages m
                WHERE m.user_id = ? AND m.id IN ({placeholders})
                """,
                (user_id, *ids),
            )
            rows = await cursor.fetchall()

        messages = []
        for row in rows:
            record_json = decrypt_message(
                dek,
                user_id,
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            rec = orjson.loads(record_json)
            messages.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "message": rec.get("message", ""),
                    "meta": rec.get("meta", {}),
                }
            )
        return messages

    async def get_history(self, user_id, created_date, dek):
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT m.id, m.role, m.reply_to, m.nonce, m.ciphertext, m.alg AS msg_alg,
                       x.ulid AS tag_ulid,
                       t.tag_hash, t.name_ct, t.name_nonce, t.alg AS tag_alg
                FROM messages m
                LEFT JOIN tag_message_xref x ON x.message_id = m.id AND x.user_id = ?
                LEFT JOIN tags t ON t.user_id = x.user_id AND t.tag_hash = x.tag_hash
                WHERE m.user_id = ? AND m.created_date = ?
                ORDER BY m.id ASC, x.ulid ASC
                """,
                (user_id, user_id, created_date),
            )
            rows = await cursor.fetchall()

        history = []
        current = None
        for row in rows:
            msg_id = row["id"]
            if not history or history[-1]["id"] != msg_id:
                record_json = decrypt_message(
                    dek,
                    user_id,
                    msg_id,
                    row["nonce"],
                    row["ciphertext"],
                    row["msg_alg"],
                )
                rec = orjson.loads(record_json)
                current = {
                    "id": msg_id,
                    "role": row["role"],
                    "reply_to": row["reply_to"],
                    "message": rec.get("message", ""),
                    "meta": rec.get("meta", {}),
                    "tags": [],
                }
                history.append(current)
            if row["tag_hash"] is not None:
                tag_name = _cached_tag_name(
                    user_id,
                    row["tag_hash"],
                    row["name_nonce"],
                    row["name_ct"],
                    row["tag_alg"].encode(),
                    dek,
                )
                current["tags"].append(
                    {"name": tag_name, "hash": row["tag_hash"].hex()}
                )
        return history

    async def get_days_with_messages(self, user_id: str, year: int, month: int) -> list[int]:
        month_prefix = f"{year:04d}-{month:02d}"
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT CAST(substr(created_date, 9, 2) AS INTEGER) AS day
                FROM messages
                WHERE user_id = ? AND substr(created_date, 1, 7) = ?
                """,
                (user_id, month_prefix),
            )
            rows = await cursor.fetchall()
        return [row["day"] for row in rows]
