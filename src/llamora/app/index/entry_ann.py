import asyncio
import logging
import time
from typing import Dict, Iterable, Optional, cast

import hnswlib
import numpy as np

from llamora.app.embed.model import async_embed_texts


logger = logging.getLogger(__name__)


class EntryIndex:
    """In-memory ANN index for a single user's entries."""

    def __init__(self, dim: int, max_elements: int = 100000):
        self.index = hnswlib.Index(space="cosine", dim=dim)
        self.index.init_index(max_elements=max_elements, ef_construction=200, M=32)
        self.index.set_ef(64)
        self.id_to_idx: Dict[str, int] = {}
        self.idx_to_id: Dict[int, str] = {}
        self.next_idx = 0
        self.last_used = time.monotonic()
        self.max_elements = max_elements

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def contains(self, entry_id: str) -> bool:
        """Return True if entry_id already indexed."""
        return entry_id in self.id_to_idx

    def add_batch(self, ids: list[str], vecs: np.ndarray) -> None:
        if not ids:
            return
        vecs = np.asarray(vecs, dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        if vecs.shape[0] != len(ids):
            raise ValueError("ids and vecs length mismatch")

        pairs = [(mid, vec) for mid, vec in zip(ids, vecs) if mid not in self.id_to_idx]
        if not pairs:
            return

        id_list, vec_list = zip(*pairs)
        ids = list(id_list)
        vecs = np.asarray(vec_list, dtype=np.float32)

        self.touch()

        required = self.next_idx + len(ids)
        current_capacity = self.max_elements
        if required > current_capacity:
            new_capacity = max(current_capacity * 2, required)
            logger.warning(
                "Resizing entry index from %d to %d to accommodate %d new items",
                current_capacity,
                new_capacity,
                len(ids),
            )
            self.index.resize_index(new_capacity)
            self.max_elements = new_capacity

        idxs = np.arange(self.next_idx, self.next_idx + len(ids))
        logger.debug("Adding %d vectors starting at index %d", len(ids), self.next_idx)
        self.index.add_items(vecs, idxs)
        for entry_id, idx in zip(ids, idxs):
            self.id_to_idx[entry_id] = int(idx)
            self.idx_to_id[int(idx)] = entry_id
        self.next_idx += len(ids)

    def search(self, query_vec: np.ndarray, k: int) -> tuple[list[str], np.ndarray]:
        self.touch()
        query_vec = np.asarray(query_vec, dtype=np.float32)
        if query_vec.ndim == 1:
            query_vec = query_vec.reshape(1, -1)
        count = self.index.get_current_count()
        if count == 0:
            logger.debug("Search invoked on empty index")
            return [], np.array([], dtype=np.float32)
        k = min(k, count)
        ef = max(k, 64)
        self.index.set_ef(ef)
        logger.debug("Searching %d vectors with k=%d ef=%d", count, k, ef)
        labels_arr, dists = cast(
            tuple[np.ndarray, np.ndarray], self.index.knn_query(query_vec, k=k)
        )
        ids: list[str] = []
        for label in labels_arr[0]:
            idx_label = int(label)
            entry_id = self.idx_to_id.get(idx_label)
            if entry_id is not None:
                ids.append(entry_id)
        return ids, dists[0][: len(ids)]

    def remove_ids(self, ids: Iterable[str]) -> None:
        removed = False
        for entry_id in ids:
            idx = self.id_to_idx.pop(entry_id, None)
            if idx is None:
                continue
            self.idx_to_id.pop(idx, None)
            if hasattr(self.index, "mark_deleted"):
                self.index.mark_deleted(idx)
            removed = True
        if removed:
            self.touch()


class EntryIndexStore:
    """Manages per-user ANN indexes, persistence and maintenance."""

    def __init__(
        self,
        db,
        ttl: int = 600,
        warm_limit: int = 1000,
        maintenance_interval: float = 60.0,
        max_elements: int = 100_000,
    ):
        self.db = db
        self.ttl = ttl
        self.warm_limit = warm_limit
        self.maintenance_interval = maintenance_interval
        self.max_elements = max_elements
        self.indexes: Dict[str, EntryIndex] = {}
        self.cursors: Dict[str, Optional[str]] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self._next_maintenance = time.monotonic() + maintenance_interval
        self._default_dim: Optional[int] = None
        self._default_dim_lock = asyncio.Lock()

    def _get_lock(self, user_id: str) -> asyncio.Lock:
        return self.locks.setdefault(user_id, asyncio.Lock())

    async def _get_default_dim(self) -> int:
        if self._default_dim is not None:
            return self._default_dim

        async with self._default_dim_lock:
            if self._default_dim is None:
                self._default_dim = (await async_embed_texts([""])).shape[1]
        if self._default_dim is None:  # pragma: no cover - defensive
            raise RuntimeError("Failed to determine embedding dimension")
        return self._default_dim

    async def _embed_and_store(
        self,
        user_id: str,
        entries: Iterable[dict],
        dek: bytes,
        idx: Optional[EntryIndex],
    ) -> EntryIndex:
        """Embed entries, add to index and persist vectors."""

        entry_list = list(entries)
        if not entry_list:
            if idx is not None:
                return idx
            dim = await self._get_default_dim()
            fresh = EntryIndex(dim, self.max_elements)
            self.indexes[user_id] = fresh
            return fresh

        texts = [entry["text"] for entry in entry_list]
        ids = [entry["id"] for entry in entry_list]
        vecs = await async_embed_texts(texts)
        if idx is None:
            dim = vecs.shape[1]
            idx = EntryIndex(dim, self.max_elements)
        idx.add_batch(ids, vecs)
        await self.db.vectors.store_vectors_batch(
            user_id,
            [(mid, vec) for mid, vec in zip(ids, vecs)],
            dek,
        )
        if entry_list:
            cursor = entry_list[-1]["id"]
            existing = self.cursors.get(user_id)
            if existing is None or cursor < existing:
                self.cursors[user_id] = cursor
        self.indexes[user_id] = idx
        return idx

    async def ensure_index(self, user_id: str, dek: bytes) -> EntryIndex:
        logger.debug("Ensuring index for user %s", user_id)
        lock = self._get_lock(user_id)
        async with lock:
            idx = self.indexes.get(user_id)
            if idx:
                logger.debug("Cache hit for user %s", user_id)
                idx.touch()
                return idx

            rows = await self.db.vectors.get_latest_vectors(
                user_id, self.warm_limit, dek
            )
            if rows:
                logger.debug(
                    "Warming index for user %s with %d vectors", user_id, len(rows)
                )
                dim = rows[0]["vec"].shape[0]
                idx = EntryIndex(dim, self.max_elements)
                ids = [r["id"] for r in rows]
                vecs = np.array([r["vec"] for r in rows], dtype=np.float32)
                if vecs.ndim == 1:
                    vecs = vecs.reshape(1, -1)
                idx.add_batch(ids, vecs)
                cursor = rows[-1]["id"]
                self.cursors[user_id] = cursor
                self.indexes[user_id] = idx

                entries = await self.db.entries.get_latest_entries(
                    user_id, self.warm_limit, dek
                )
                missing = [entry for entry in entries if not idx.contains(entry["id"])]
                if missing:
                    logger.debug(
                        "Embedding %d entries missing vectors for user %s",
                        len(missing),
                        user_id,
                    )
                    await self._embed_and_store(user_id, missing, dek, idx)
                return idx

            entries = await self.db.entries.get_latest_entries(
                user_id, self.warm_limit, dek
            )
            if entries:
                logger.debug(
                    "Embedding and indexing %d entries for user %s",
                    len(entries),
                    user_id,
                )
                idx = await self._embed_and_store(user_id, entries, dek, None)
                return idx

            logger.debug("No existing data for user %s, creating empty index", user_id)
            dim = await self._get_default_dim()
            idx = EntryIndex(dim, self.max_elements)
            latest = await self.db.entries.get_user_latest_entry_id(user_id)
            self.cursors[user_id] = latest
            self.indexes[user_id] = idx
            return idx

    async def expand_older(self, user_id: str, dek: bytes, batch: int) -> int:
        await self.ensure_index(user_id, dek)
        lock = self._get_lock(user_id)
        async with lock:
            cursor = self.cursors.get(user_id)
            if not cursor:
                logger.debug("No cursor for user %s", user_id)
                return 0

            idx = self.indexes.get(user_id)
            if idx is None:
                logger.debug("Index missing for user %s", user_id)
                return 0

            added = 0
            new_cursor = cursor

            rows = await self.db.vectors.get_vectors_older_than(
                user_id, cursor, batch, dek
            )
            if rows:
                logger.debug(
                    "Loaded %d stored vectors older than %s for user %s",
                    len(rows),
                    cursor,
                    user_id,
                )
                ids = [r["id"] for r in rows]
                vecs = np.array([r["vec"] for r in rows], dtype=np.float32)
                if vecs.ndim == 1:
                    vecs = vecs.reshape(1, -1)
                idx.add_batch(ids, vecs)
                added += len(ids)
                new_cursor = rows[-1]["id"]

            entries = await self.db.entries.get_entries_older_than(
                user_id, cursor, batch, dek
            )
            missing = [entry for entry in entries if not idx.contains(entry["id"])]
            if missing:
                logger.debug(
                    "Embedding %d entries older than %s for user %s",
                    len(missing),
                    cursor,
                    user_id,
                )
                await self._embed_and_store(user_id, missing, dek, idx)
                added += len(missing)
                new_cursor = missing[-1]["id"]

            if added == 0:
                logger.debug("No older entries for user %s", user_id)
                return 0

            existing = self.cursors.get(user_id)
            if existing is None or new_cursor < existing:
                self.cursors[user_id] = new_cursor
            return added

    async def hydrate_entries(
        self, user_id: str, entry_ids: list[str], dek: bytes
    ) -> list[dict]:
        if not entry_ids:
            return []
        rows = await self.db.entries.get_entries_by_ids(user_id, entry_ids, dek)
        return rows

    async def index_entry(
        self, user_id: str, entry_id: str, content: str, dek: bytes
    ) -> None:
        await self.bulk_index([(user_id, entry_id, content, dek)])

    async def bulk_index(self, entries: Iterable[tuple[str, str, str, bytes]]) -> None:
        items = list(entries)
        if not items:
            return

        texts = [content for _, _, content, _ in items]
        vecs = await async_embed_texts(texts)
        if vecs.shape[0] != len(items):  # pragma: no cover - defensive
            raise ValueError("Embedding count does not match input items")

        per_user: Dict[str, list[tuple[str, np.ndarray, bytes]]] = {}
        for (user_id, entry_id, _, dek), vec in zip(items, vecs):
            per_user.setdefault(user_id, []).append((entry_id, vec, dek))

        logger.debug(
            "Bulk indexing %d entries across %d users", len(items), len(per_user)
        )

        for user_id, user_entries in per_user.items():
            dek = user_entries[0][2]
            idx = await self.ensure_index(user_id, dek)
            lock = self._get_lock(user_id)
            ids = [entry_id for entry_id, _, _ in user_entries]
            vec_arr = np.asarray([vec for _, vec, _ in user_entries], dtype=np.float32)
            async with lock:
                await self.db.vectors.store_vectors_batch(
                    user_id,
                    [(entry_id, vec) for entry_id, vec, _ in user_entries],
                    dek,
                )
                idx.add_batch(ids, vec_arr)
                for entry_id in ids:
                    current = self.cursors.get(user_id)
                    if current is None or entry_id < current:
                        self.cursors[user_id] = entry_id
                self.indexes[user_id] = idx

    def _evict_idle(self) -> None:
        now = time.monotonic()
        to_remove = [
            uid for uid, idx in self.indexes.items() if now - idx.last_used > self.ttl
        ]
        if to_remove:
            logger.debug("Evicting %d idle indexes", len(to_remove))
        for uid in to_remove:
            self.indexes.pop(uid, None)
            self.cursors.pop(uid, None)
            lock = self.locks.pop(uid, None)
            if lock and lock.locked():
                logger.debug("Lock for user %s remained locked during eviction", uid)

    async def maintenance(self) -> None:
        now = time.monotonic()
        if now < self._next_maintenance:
            return
        self._next_maintenance = now + self.maintenance_interval
        self._evict_idle()

    async def remove_entries(self, user_id: str, entry_ids: Iterable[str]) -> None:
        ids = [entry_id for entry_id in entry_ids if entry_id]
        if not ids:
            return
        idx = self.indexes.get(user_id)
        if not idx:
            return
        lock = self._get_lock(user_id)
        async with lock:
            idx.remove_ids(ids)
