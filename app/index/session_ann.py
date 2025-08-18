import asyncio
import logging
import time
from typing import Dict

import hnswlib
import numpy as np

from app.embed.model import embed_texts


logger = logging.getLogger(__name__)


class SessionIndex:
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

        pairs = [
            (mid, vec) for mid, vec in zip(ids, vecs) if mid not in self.id_to_idx
        ]
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


class SessionIndexRegistry:
    """Keeps per-user ANN indexes in RAM and evicts idle ones."""

    def __init__(self, db, ttl: int = 600, warm_limit: int = 1000):
        self.db = db
        self.ttl = ttl
        self.warm_limit = warm_limit
        self.indexes: Dict[str, SessionIndex] = {}
        self.cursors: Dict[str, str] = {}
        self.locks: Dict[str, asyncio.Lock] = {}

    async def get_or_build(self, user_id: str, dek: bytes) -> SessionIndex:
        logger.debug("Fetching index for user %s", user_id)
        lock = self.locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            idx = self.indexes.get(user_id)
            if idx:
                logger.debug("Cache hit for user %s", user_id)
                idx.touch()
                return idx

            rows = await self.db.get_latest_vectors(user_id, self.warm_limit, dek)
            if rows:
                logger.debug(
                    "Warming index for user %s with %d vectors", user_id, len(rows)
                )
                dim = rows[0]["vec"].shape[0]
                idx = SessionIndex(dim)
                ids = [r["id"] for r in rows]
                vecs = np.array([r["vec"] for r in rows], dtype=np.float32)
                if vecs.ndim == 1:
                    vecs = vecs.reshape(1, -1)
                idx.add_batch(ids, vecs)
                self.cursors[user_id] = rows[-1]["id"]
                self.indexes[user_id] = idx
                return idx

            # Fallback: no stored vectors, warm from latest messages
            msgs = await self.db.get_latest_messages(user_id, self.warm_limit, dek)
            if msgs:
                logger.debug(
                    "Embedding and indexing %d messages for user %s", len(msgs), user_id
                )
                texts: list[str] = []
                ids: list[str] = []
                sess_ids: list[str] = []
                for msg in msgs:
                    texts.append(msg["message"])
                    ids.append(msg["id"])
                    sess_ids.append(msg["session_id"])

                vecs = embed_texts(texts).astype(np.float32)
                dim = vecs.shape[1]
                idx = SessionIndex(dim)
                idx.add_batch(ids, vecs)
                for mid, sid, vec in zip(ids, sess_ids, vecs):
                    await self.db.store_vector(mid, user_id, sid, vec, dek)
                self.cursors[user_id] = msgs[-1]["id"]
                self.indexes[user_id] = idx
                return idx

            logger.debug("No existing data for user %s, creating empty index", user_id)
            dim = embed_texts([""]).shape[1]
            idx = SessionIndex(dim)
            latest = await self.db.get_user_latest_id(user_id)
            self.cursors[user_id] = latest
            self.indexes[user_id] = idx
            return idx

    async def expand_older(self, user_id: str, dek: bytes, batch: int) -> int:
        await self.get_or_build(user_id, dek)
        lock = self.locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            cursor = self.cursors.get(user_id)
            if not cursor:
                logger.debug("No cursor for user %s", user_id)
                return 0

            idx = self.indexes.get(user_id)
            if idx is None:
                logger.debug("Index missing for user %s", user_id)
                return 0

            rows = await self.db.get_vectors_older_than(user_id, cursor, batch, dek)
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
                self.cursors[user_id] = rows[-1]["id"]
                return len(ids)

            msgs = await self.db.get_messages_older_than(user_id, cursor, batch, dek)
            if msgs:
                logger.debug(
                    "Embedding %d messages older than %s for user %s",
                    len(msgs),
                    cursor,
                    user_id,
                )
                texts: list[str] = []
                ids: list[str] = []
                sess_ids: list[str] = []
                for msg in msgs:
                    texts.append(msg["message"])
                    ids.append(msg["id"])
                    sess_ids.append(msg["session_id"])

                vecs = embed_texts(texts).astype(np.float32)
                idx.add_batch(ids, vecs)
                for mid, sid, vec in zip(ids, sess_ids, vecs):
                    await self.db.store_vector(mid, user_id, sid, vec, dek)
                self.cursors[user_id] = msgs[-1]["id"]
                return len(ids)

            logger.debug("No older messages for user %s", user_id)
            return 0

    def evict_idle(self) -> None:
        now = time.monotonic()
        to_remove = [
            uid for uid, idx in self.indexes.items() if now - idx.last_used > self.ttl
        ]
        if to_remove:
            logger.debug("Evicting %d idle indexes", len(to_remove))
        for uid in to_remove:
            self.indexes.pop(uid, None)
