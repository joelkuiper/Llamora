import asyncio
import logging
from pathlib import Path
from typing import Any, TypeVar, cast
from collections.abc import Coroutine

import aiosqlite
from aiosqlitepool import SQLiteConnectionPool
from aiosqlitepool.protocols import Connection as SQLitePoolConnection

from llamora.settings import settings
from llamora.util import resolve_data_path

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


SCHEMA_PATH = resolve_data_path(
    "sql/schema.sql",
    fallback_dir=Path(__file__).resolve().parents[3] / "sql",
)


class LocalDB:
    """Facade around SQLite repositories with shared connection pooling."""

    def __init__(self, db_path: str | None = None):
        raw_path = Path(db_path or settings.DATABASE.path)
        self.db_path = raw_path.expanduser().resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.pool: SQLiteConnectionPool | None = None
        self.search_api = None
        self._users: UsersRepository | None = None
        self._messages: MessagesRepository | None = None
        self._tags: TagsRepository | None = None
        self._vectors: VectorsRepository | None = None
        self._search_history: SearchHistoryRepository | None = None
        self._events: RepositoryEventBus | None = None

    async def __aenter__(self) -> "LocalDB":
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def __enter__(self) -> "LocalDB":
        self._run_sync(self.init())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._run_sync(self.close())

    def _run_sync(self, operation: Coroutine[Any, Any, Any]) -> Any:
        if hasattr(asyncio, "Runner"):
            with asyncio.Runner() as runner:  # type: ignore[attr-defined]
                return runner.run(operation)

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(operation)
        finally:
            loop.close()

    def set_search_api(self, api) -> None:
        self.search_api = api
        if self._messages:
            self._messages.set_on_message_appended(self._on_message_appended)

    async def init(self) -> None:
        if self.pool is not None:
            return

        is_new = not self.db_path.exists()
        acquisition_timeout = int(settings.DATABASE.pool_acquire_timeout)

        async def _connection_factory() -> SQLitePoolConnection:
            return cast(SQLitePoolConnection, await self._create_connection())

        pool = SQLiteConnectionPool(
            _connection_factory,
            pool_size=int(settings.DATABASE.pool_size),
            acquisition_timeout=acquisition_timeout,
        )
        self.pool = pool
        try:
            await self._ensure_schema(is_new)
            self._configure_repositories()
        except Exception:
            await pool.close()
            self.pool = None
            self._users = None
            self._messages = None
            self._tags = None
            self._vectors = None
            self._search_history = None
            self._events = None
            raise

    async def close(self) -> None:
        if self.pool is not None:
            try:
                await self.pool.close()
            finally:
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
        await conn.execute(
            f"PRAGMA busy_timeout = {int(settings.DATABASE.busy_timeout)}"
        )
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
        schema_sql = SCHEMA_PATH.read_text().format(
            max_username_length=int(settings.LIMITS.max_username_length)
        )
        async with self.pool.connection() as conn:
            await run_in_transaction(
                conn,
                conn.executescript,
                schema_sql,
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
