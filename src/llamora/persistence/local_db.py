import asyncio
import atexit
import logging
import os
from typing import TypeVar, cast

import aiosqlite
from aiosqlitepool import SQLiteConnectionPool
from aiosqlitepool.protocols import Connection as SQLitePoolConnection

from llamora.settings import settings

from llamora.app.services.crypto import (
    encrypt_message,
    decrypt_message,
    encrypt_vector,
    decrypt_vector,
)
from llamora.app.db.base import run_in_transaction
from llamora.app.db.events import RepositoryEventBus
from llamora.app.db.users import UsersRepository
from llamora.app.db.messages import MessagesRepository
from llamora.app.db.tags import TagsRepository
from llamora.app.db.vectors import VectorsRepository
from llamora.app.db.search_history import SearchHistoryRepository


RepositoryT = TypeVar("RepositoryT")


class LocalDB:
    """Facade around SQLite repositories with shared connection pooling."""

    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or settings.DATABASE.path)
        self.pool: SQLiteConnectionPool | None = None
        self.search_api = None
        self._users: UsersRepository | None = None
        self._messages: MessagesRepository | None = None
        self._tags: TagsRepository | None = None
        self._vectors: VectorsRepository | None = None
        self._search_history: SearchHistoryRepository | None = None
        self._events: RepositoryEventBus | None = None
        atexit.register(self._atexit_close)

    def _atexit_close(self) -> None:
        if self.pool is None:
            return

        logger = logging.getLogger(__name__)
        pool = self.pool

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is None:
                try:
                    asyncio.run(pool.close())
                except RuntimeError:
                    new_loop = asyncio.new_event_loop()
                    try:
                        new_loop.run_until_complete(pool.close())
                    finally:
                        new_loop.close()
            elif loop.is_running():
                loop.create_task(pool.close())
            else:
                loop.run_until_complete(pool.close())
        except Exception:
            logger.exception("Failed to close SQLite connection pool during shutdown")
        else:
            self.pool = None

    def __del__(self):
        self._atexit_close()

    def set_search_api(self, api) -> None:
        self.search_api = api
        if self._messages:
            self._messages.set_on_message_appended(self._on_message_appended)

    async def init(self) -> None:
        is_new = not os.path.exists(self.db_path)
        acquisition_timeout = int(settings.DATABASE.pool_acquire_timeout)

        async def _connection_factory() -> SQLitePoolConnection:
            return cast(SQLitePoolConnection, await self._create_connection())

        self.pool = SQLiteConnectionPool(
            _connection_factory,
            pool_size=int(settings.DATABASE.pool_size),
            acquisition_timeout=acquisition_timeout,
        )
        await self._ensure_schema(is_new)
        self._configure_repositories()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
        self._users = None
        self._messages = None
        self._tags = None
        self._vectors = None
        self._search_history = None
        self._events = None

    async def _create_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(
            self.db_path, timeout=float(settings.DATABASE.timeout)
        )
        await conn.execute(f"PRAGMA busy_timeout = {int(settings.DATABASE.busy_timeout)}")
        await conn.execute(f"PRAGMA mmap_size = {int(settings.DATABASE.mmap_size)}")
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.execute("PRAGMA cache_size = 10000")
        await conn.execute("PRAGMA temp_store = MEMORY")
        conn.row_factory = aiosqlite.Row
        return conn

    async def _ensure_schema(self, is_new: bool) -> None:
        if is_new:
            logging.getLogger(__name__).info(
                "Creating new database at %s", self.db_path
            )
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        async with self.pool.connection() as conn:
            await run_in_transaction(
                conn,
                conn.executescript,
                f"""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL CHECK(length(username) <= {int(settings.LIMITS.max_username_length)}),
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

                CREATE TABLE IF NOT EXISTS search_history (
                    user_id TEXT NOT NULL,
                    query_hash BLOB(32) NOT NULL,
                    query_nonce BLOB(24) NOT NULL,
                    query_ct BLOB NOT NULL,
                    alg TEXT NOT NULL,
                    usage_count INTEGER DEFAULT 1,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(user_id, query_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_user_date ON messages(user_id, created_date);
                CREATE INDEX IF NOT EXISTS idx_messages_reply_to ON messages(reply_to);
                CREATE INDEX IF NOT EXISTS idx_vectors_user_id ON vectors(user_id);

                CREATE INDEX IF NOT EXISTS idx_tag_message_hash ON tag_message_xref(user_id, tag_hash);
                CREATE INDEX IF NOT EXISTS idx_tag_message_message ON tag_message_xref(user_id, message_id);
                CREATE INDEX IF NOT EXISTS idx_search_history_user_last_used
                    ON search_history(user_id, last_used DESC);
                """,
            )

    def _configure_repositories(self) -> None:
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        self._events = RepositoryEventBus()
        self._users = UsersRepository(self.pool)
        self._messages = MessagesRepository(
            self.pool, encrypt_message, decrypt_message, self._events
        )
        self._tags = TagsRepository(
            self.pool,
            encrypt_message,
            decrypt_message,
            self._events,
        )
        self._vectors = VectorsRepository(self.pool, encrypt_vector, decrypt_vector)
        self._search_history = SearchHistoryRepository(
            self.pool, encrypt_message, decrypt_message
        )
        self._messages.set_on_message_appended(self._on_message_appended)

    def _require_repository(
        self, repository: RepositoryT | None, name: str
    ) -> RepositoryT:
        if repository is None:
            raise RuntimeError(
                f"{name} repository is not initialised; call init() before accessing it."
            )
        return repository

    @property
    def users(self) -> UsersRepository:
        """Return the users repository.

        Raises a :class:`RuntimeError` when accessed before the database has been
        initialised so configuration errors are caught early.
        """

        return self._require_repository(self._users, "Users")

    @property
    def messages(self) -> MessagesRepository:
        """Return the messages repository."""

        return self._require_repository(self._messages, "Messages")

    @property
    def tags(self) -> TagsRepository:
        """Return the tags repository."""

        return self._require_repository(self._tags, "Tags")

    @property
    def vectors(self) -> VectorsRepository:
        """Return the vectors repository."""

        return self._require_repository(self._vectors, "Vectors")

    @property
    def search_history(self) -> SearchHistoryRepository:
        """Return the search history repository."""

        return self._require_repository(self._search_history, "Search history")

    async def _on_message_appended(
        self, user_id: str, message_id: str, plaintext: str, dek: bytes
    ) -> None:
        if self.search_api:
            await self.search_api.enqueue_index_job(user_id, message_id, plaintext, dek)
