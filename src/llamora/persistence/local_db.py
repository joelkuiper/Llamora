from __future__ import annotations

# SQLite persistence helpers with hardened connection pragmas.

import asyncio
import logging
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar, cast

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
from llamora.app.services.migrations import run_db_migrations
from llamora.app.db.events import RepositoryEventBus
from llamora.app.services.history_cache import HistoryCache, HistoryCacheSynchronizer
from llamora.app.services.tag_recall import (
    TAG_RECALL_SUMMARY_CACHE,
    TagRecallCacheSynchronizer,
)
from llamora.app.db.users import UsersRepository
from llamora.app.db.entries import EntriesRepository
from llamora.app.db.tags import TagsRepository
from llamora.app.db.vectors import VectorsRepository
from llamora.app.db.search_history import SearchHistoryRepository


RepositoryT = TypeVar("RepositoryT")

logger = logging.getLogger(__name__)


class LocalDB:
    """Facade around SQLite repositories with shared connection pooling."""

    def __init__(self, db_path: str | None = None):
        raw_path = Path(db_path or settings.DATABASE.path)
        self.db_path = raw_path.expanduser().resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.pool: SQLiteConnectionPool | None = None
        self.search_api = None
        self._users: UsersRepository | None = None
        self._entries: EntriesRepository | None = None
        self._tags: TagsRepository | None = None
        self._vectors: VectorsRepository | None = None
        self._search_history: SearchHistoryRepository | None = None
        self._events: RepositoryEventBus | None = None
        self._history_cache: HistoryCache | None = None
        self._history_synchronizer: HistoryCacheSynchronizer | None = None
        self._tag_recall_synchronizer: TagRecallCacheSynchronizer | None = None
        self._init_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

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
        with self._sync_lock:
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
        if self._entries:
            self._entries.set_on_entry_appended(self._on_entry_appended)

    async def init(self) -> None:
        async with self._init_lock:
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
                self._entries = None
                self._tags = None
                self._vectors = None
                self._search_history = None
                self._events = None
                self._history_cache = None
                self._tag_recall_synchronizer = None
                raise

    async def close(self) -> None:
        async with self._init_lock:
            if self.pool is not None:
                try:
                    await self.pool.close()
                finally:
                    self.pool = None
            self._users = None
            self._entries = None
            self._tags = None
            self._vectors = None
            self._search_history = None
            self._events = None
            self._history_cache = None
            self._tag_recall_synchronizer = None

    async def _create_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(
            self.db_path, timeout=float(settings.DATABASE.timeout)
        )
        pragmas = (
            f"PRAGMA busy_timeout = {int(settings.DATABASE.busy_timeout)}",
            f"PRAGMA mmap_size = {int(settings.DATABASE.mmap_size)}",
            "PRAGMA foreign_keys = ON",
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA cache_size = 10000",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA trusted_schema = OFF",
        )
        for pragma in pragmas:
            await self._apply_pragma(conn, pragma)
        conn.row_factory = aiosqlite.Row
        return conn

    async def _apply_pragma(self, conn: aiosqlite.Connection, pragma: str) -> None:
        try:
            await conn.execute(pragma)
        except Exception:
            logger.warning("Failed to apply %s", pragma, exc_info=True)

    async def _ensure_schema(self, is_new: bool) -> None:
        if is_new:
            logger.info("Preparing database at %s", self.db_path)
        await run_db_migrations(self.db_path, verbose=False)

    def _configure_repositories(self) -> None:
        if not self.pool:
            raise RuntimeError("Connection pool not initialized")
        self._events = RepositoryEventBus()
        history_cache_cfg = settings.MESSAGES.history_cache
        self._history_cache = HistoryCache(
            maxsize=int(history_cache_cfg.maxsize),
            ttl=int(history_cache_cfg.ttl),
        )
        self._users = UsersRepository(self.pool)
        self._entries = EntriesRepository(
            self.pool,
            encrypt_message,
            decrypt_message,
            self._events,
            self._history_cache,
        )
        self._history_synchronizer = HistoryCacheSynchronizer(
            event_bus=self._events,
            history_cache=self._history_cache,
            entries_repository=self._entries,
        )
        # Tag recall summary cache is keyed by summary input digest, so we do
        # not invalidate on tag changes; cache misses occur naturally when the
        # aggregated input changes.
        self._tag_recall_synchronizer = None
        self._tags = TagsRepository(
            self.pool,
            encrypt_message,
            decrypt_message,
            self._events,
        )
        self._tag_recall_synchronizer = TagRecallCacheSynchronizer(
            event_bus=self._events,
            entries_repository=self._entries,
            cache=TAG_RECALL_SUMMARY_CACHE,
        )
        self._vectors = VectorsRepository(self.pool, encrypt_vector, decrypt_vector)
        self._search_history = SearchHistoryRepository(
            self.pool, encrypt_message, decrypt_message
        )
        self._entries.set_on_entry_appended(self._on_entry_appended)

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
    def entries(self) -> EntriesRepository:
        """Return the entries repository."""

        return self._require_repository(self._entries, "Entries")

    @property
    def history_cache(self) -> HistoryCache:
        """Expose the shared entry history cache."""

        return self._require_repository(self._history_cache, "History cache")

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

    async def _on_entry_appended(
        self, user_id: str, entry_id: str, plaintext: str, dek: bytes
    ) -> None:
        if self.search_api:
            await self.search_api.enqueue_index_job(user_id, entry_id, plaintext, dek)
