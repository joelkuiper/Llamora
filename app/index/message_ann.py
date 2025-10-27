import asyncio
import logging
import time
from typing import Dict, Iterable, Optional

import hnswlib
import numpy as np

from app.embed.model import async_embed_texts


logger = logging.getLogger(__name__)


class MessageIndex:
    """In-memory ANN index for a single user's messages."""

    def __init__(self, dim: int, max_elements: int = 100000):
        self.index = hnswlib.Index(space="cosine", dim=dim)
        self.index.init_index(max_elements=max_elements, ef_construction=200, M=32)
        self.index.set_ef(64)
        self.id_to_idx: Dict[str, int] = {}
        self.idx_to_id: Dict[int, str] = {}
        self.next_idx = 0
        self.last_used = time.monotonic()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def contains(self, msg_id: str) -> bool:
        """Return True if msg_id already indexed."""
        return msg_id in self.id_to_idx

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

        ids, vecs = zip(*pairs)
        vecs = np.asarray(vecs, dtype=np.float32)

        self.touch()
        idxs = np.arange(self.next_idx, self.next_idx + len(ids))
        logger.debug("Adding %d vectors starting at index %d", len(ids), self.next_idx)
        self.index.add_items(vecs, idxs)
        for msg_id, idx in zip(ids, idxs):
            self.id_to_idx[msg_id] = int(idx)
            self.idx_to_id[int(idx)] = msg_id
        self.next_idx += len(ids)

    def search(self, query_vec: np.ndarray, k: int):
        self.touch()
        query_vec = np.asarray(query_vec, dtype=np.float32)
        if query_vec.ndim == 1:
            query_vec = query_vec.reshape(1, -1)
        count = self.index.get_current_count()
        if count == 0:
            logger.debug("Search invoked on empty index")
            return [], []
        k = min(k, count)
        ef = max(k, 64)
        self.index.set_ef(ef)
        logger.debug("Searching %d vectors with k=%d ef=%d", count, k, ef)
        labels, dists = self.index.knn_query(query_vec, k=k)
        ids = [
            self.idx_to_id.get(int(label))
            for label in labels[0]
            if int(label) in self.idx_to_id
        ]
        return ids, dists[0][: len(ids)]


class MessageIndexStore:
    """Manages per-user ANN indexes, persistence and maintenance."""

    def __init__(
        self,
        db,
        ttl: int = 600,
        warm_limit: int = 1000,
        maintenance_interval: float = 60.0,
    ):
        self.db = db
        self.ttl = ttl
        self.warm_limit = warm_limit
        self.maintenance_interval = maintenance_interval
        self.indexes: Dict[str, MessageIndex] = {}
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
        return self._default_dim

    async def _embed_and_store(
        self,
        user_id: str,
        msgs: Iterable[dict],
        dek: bytes,
        idx: Optional[MessageIndex],
    ) -> MessageIndex:
        """Embed messages, add to index and persist vectors."""

        msg_list = list(msgs)
        if not msg_list:
            if idx is not None:
                return idx
            dim = await self._get_default_dim()
            fresh = MessageIndex(dim)
            self.indexes[user_id] = fresh
            return fresh

        texts = [m["message"] for m in msg_list]
        ids = [m["id"] for m in msg_list]
        vecs = (await async_embed_texts(texts)).astype(np.float32)
        if idx is None:
            dim = vecs.shape[1]
            idx = MessageIndex(dim)
        idx.add_batch(ids, vecs)
        await self.db.vectors.store_vectors_batch(
            user_id,
            [(mid, vec) for mid, vec in zip(ids, vecs)],
            dek,
        )
        if msg_list:
            cursor = msg_list[-1]["id"]
            existing = self.cursors.get(user_id)
            if existing is None or cursor < existing:
                self.cursors[user_id] = cursor
        self.indexes[user_id] = idx
        return idx

    async def ensure_index(self, user_id: str, dek: bytes) -> MessageIndex:
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
                idx = MessageIndex(dim)
                ids = [r["id"] for r in rows]
                vecs = np.array([r["vec"] for r in rows], dtype=np.float32)
                if vecs.ndim == 1:
                    vecs = vecs.reshape(1, -1)
                idx.add_batch(ids, vecs)
                cursor = rows[-1]["id"]
                self.cursors[user_id] = cursor
                self.indexes[user_id] = idx

                msgs = await self.db.messages.get_latest_messages(
                    user_id, self.warm_limit, dek
                )
                missing = [m for m in msgs if not idx.contains(m["id"])]
                if missing:
                    logger.debug(
                        "Embedding %d messages missing vectors for user %s",
                        len(missing),
                        user_id,
                    )
                    await self._embed_and_store(user_id, missing, dek, idx)
                return idx

            msgs = await self.db.messages.get_latest_messages(
                user_id, self.warm_limit, dek
            )
            if msgs:
                logger.debug(
                    "Embedding and indexing %d messages for user %s", len(msgs), user_id
                )
                idx = await self._embed_and_store(user_id, msgs, dek, None)
                return idx

            logger.debug("No existing data for user %s, creating empty index", user_id)
            dim = await self._get_default_dim()
            idx = MessageIndex(dim)
            latest = await self.db.messages.get_user_latest_id(user_id)
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

            msgs = await self.db.messages.get_messages_older_than(
                user_id, cursor, batch, dek
            )
            missing = [m for m in msgs if not idx.contains(m["id"])]
            if missing:
                logger.debug(
                    "Embedding %d messages older than %s for user %s",
                    len(missing),
                    cursor,
                    user_id,
                )
                await self._embed_and_store(user_id, missing, dek, idx)
                added += len(missing)
                new_cursor = missing[-1]["id"]

            if added == 0:
                logger.debug("No older messages for user %s", user_id)
                return 0

            existing = self.cursors.get(user_id)
            if existing is None or new_cursor < existing:
                self.cursors[user_id] = new_cursor
            return added

    async def hydrate_messages(
        self, user_id: str, message_ids: list[str], dek: bytes
    ) -> list[dict]:
        if not message_ids:
            return []
        rows = await self.db.messages.get_messages_by_ids(user_id, message_ids, dek)
        return rows

    async def index_message(
        self, user_id: str, message_id: str, content: str, dek: bytes
    ) -> None:
        idx = await self.ensure_index(user_id, dek)
        vec = (await async_embed_texts([content])).astype(np.float32)
        lock = self._get_lock(user_id)
        async with lock:
            await self.db.vectors.store_vector(message_id, user_id, vec[0], dek)
            if not idx.contains(message_id):
                idx.add_batch([message_id], vec)
                current = self.cursors.get(user_id)
                if current is None or message_id < current:
                    self.cursors[user_id] = message_id
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
