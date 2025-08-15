import asyncio
import time
from typing import Dict

import hnswlib
import numpy as np

from app.embed.model import embed_texts
from app.services.crypto import decrypt_message, decrypt_vector, encrypt_vector


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

    def add_batch(self, ids: list[str], vecs: np.ndarray) -> None:
        if not ids:
            return
        vecs = np.asarray(vecs, dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        if vecs.shape[0] != len(ids):
            raise ValueError("ids and vecs length mismatch")
        self.touch()
        idxs = np.arange(self.next_idx, self.next_idx + len(ids))
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
        labels, dists = self.index.knn_query(query_vec, k=k)
        ids = [self.idx_to_id.get(int(l)) for l in labels[0] if int(l) in self.idx_to_id]
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
        lock = self.locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            idx = self.indexes.get(user_id)
            if idx:
                idx.touch()
                return idx

            rows = await self.db.get_latest_vectors(user_id, self.warm_limit)
            if rows:
                dim = rows[0]["dim"]
                idx = SessionIndex(dim)
                ids: list[str] = []
                vecs = []
                for row in rows:
                    vec_bytes = decrypt_vector(
                        dek,
                        user_id,
                        row["id"],
                        row["nonce"],
                        row["ciphertext"],
                        row["alg"],
                        session_id=row.get("session_id"),
                    )
                    vec = np.frombuffer(vec_bytes, dtype=np.float32).reshape(row["dim"])
                    ids.append(row["id"])
                    vecs.append(vec)
                if vecs:
                    vec_arr = np.array(vecs, dtype=np.float32)
                    if vec_arr.ndim == 1:
                        vec_arr = vec_arr.reshape(1, -1)
                    idx.add_batch(ids, vec_arr)
                    self.cursors[user_id] = rows[-1]["id"]
                self.indexes[user_id] = idx
                return idx

            # Fallback: no stored vectors, warm from latest messages
            msgs = await self.db.get_latest_messages(user_id, self.warm_limit)
            if msgs:
                texts: list[str] = []
                ids: list[str] = []
                sess_ids: list[str] = []
                for msg in msgs:
                    content = decrypt_message(
                        dek,
                        user_id,
                        msg["session_id"],
                        msg["id"],
                        msg["nonce"],
                        msg["ciphertext"],
                        msg["alg"],
                    )
                    texts.append(content)
                    ids.append(msg["id"])
                    sess_ids.append(msg["session_id"])

                vecs = embed_texts(texts).astype(np.float32)
                dim = vecs.shape[1]
                idx = SessionIndex(dim)
                for mid, sid, vec in zip(ids, sess_ids, vecs):
                    nonce, ct, alg = encrypt_vector(
                        dek, user_id, mid, vec.tobytes(), session_id=sid
                    )
                    await self.db.store_vector(mid, user_id, vec.shape[0], nonce, ct, alg)
                idx.add_batch(ids, vecs)
                self.cursors[user_id] = msgs[-1]["id"]
            else:
                idx = SessionIndex(384)
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
                return 0

            idx = self.indexes.get(user_id)
            if idx is None:
                return 0

            rows = await self.db.get_vectors_older_than(user_id, cursor, batch)
            if rows:
                ids: list[str] = []
                vecs = []
                for row in rows:
                    vec_bytes = decrypt_vector(
                        dek,
                        user_id,
                        row["id"],
                        row["nonce"],
                        row["ciphertext"],
                        row["alg"],
                        session_id=row.get("session_id"),
                    )
                    vec = np.frombuffer(vec_bytes, dtype=np.float32).reshape(row["dim"])
                    ids.append(row["id"])
                    vecs.append(vec)
                vec_arr = np.array(vecs, dtype=np.float32)
                if vec_arr.ndim == 1:
                    vec_arr = vec_arr.reshape(1, -1)
                idx.add_batch(ids, vec_arr)
                self.cursors[user_id] = rows[-1]["id"]
                return len(ids)

            msgs = await self.db.get_messages_older_than(user_id, cursor, batch)
            if msgs:
                texts: list[str] = []
                ids: list[str] = []
                sess_ids: list[str] = []
                for msg in msgs:
                    content = decrypt_message(
                        dek,
                        user_id,
                        msg["session_id"],
                        msg["id"],
                        msg["nonce"],
                        msg["ciphertext"],
                        msg["alg"],
                    )
                    texts.append(content)
                    ids.append(msg["id"])
                    sess_ids.append(msg["session_id"])

                vecs = embed_texts(texts).astype(np.float32)
                idx.add_batch(ids, vecs)
                for mid, sid, vec in zip(ids, sess_ids, vecs):
                    nonce, ct, alg = encrypt_vector(
                        dek, user_id, mid, vec.tobytes(), session_id=sid
                    )
                    await self.db.store_vector(mid, user_id, vec.shape[0], nonce, ct, alg)
                self.cursors[user_id] = msgs[-1]["id"]
                return len(ids)

            return 0

    def evict_idle(self) -> None:
        now = time.monotonic()
        to_remove = [uid for uid, idx in self.indexes.items() if now - idx.last_used > self.ttl]
        for uid in to_remove:
            self.indexes.pop(uid, None)
