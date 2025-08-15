import time
from typing import List

import numpy as np

from config import (
    PROGRESSIVE_BATCH,
    PROGRESSIVE_K1,
    PROGRESSIVE_K2,
    PROGRESSIVE_MAX_MS,
    PROGRESSIVE_ROUNDS,
    POOR_MATCH_MAX_COS,
    POOR_MATCH_MIN_HITS,
)
from app.embed.model import embed_texts
from app.index.session_ann import SessionIndexRegistry
from app.services.crypto import encrypt_vector, decrypt_message


class SearchAPI:
    """High level search interface operating on encrypted messages."""

    def __init__(self, db):
        self.db = db
        self.registry = SessionIndexRegistry(db)

    async def search(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int = PROGRESSIVE_K1,
        k2: int = PROGRESSIVE_K2,
    ):
        index = await self.registry.get_or_build(user_id, dek)
        q_vec = embed_texts([query]).astype(np.float32).reshape(1, -1)

        def quality(ids: List[str], cosines: List[float]) -> bool:
            if len(ids) < k2:
                return False
            max_cos = max(cosines) if cosines else 0.0
            hits = sum(c >= POOR_MATCH_MAX_COS for c in cosines)
            if max_cos < POOR_MATCH_MAX_COS or hits < POOR_MATCH_MIN_HITS:
                return False
            return True

        current_k1 = k1
        start = time.monotonic()
        ids, dists = index.search(q_vec, current_k1)
        cosines = [1 - d for d in dists]

        rounds = 0
        while not quality(ids, cosines):
            elapsed_ms = (time.monotonic() - start) * 1000
            if rounds >= PROGRESSIVE_ROUNDS or elapsed_ms >= PROGRESSIVE_MAX_MS:
                break
            added = await self.registry.expand_older(user_id, dek, PROGRESSIVE_BATCH)
            if added <= 0:
                break
            rounds += 1
            if rounds == 1:
                current_k1 = min(2 * current_k1, 512)
            ids, dists = index.search(q_vec, current_k1)
            cosines = [1 - d for d in dists]

        top_ids = ids[:k2]
        rows = await self.db.get_messages_by_ids(user_id, top_ids)
        row_map = {r["id"]: r for r in rows}
        results: List[dict] = []
        for mid in top_ids:
            row = row_map.get(mid)
            if not row:
                continue
            content = decrypt_message(
                dek,
                user_id,
                row["session_id"],
                row["id"],
                row["nonce"],
                row["ciphertext"],
                row["alg"],
            )
            results.append(
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "role": row["role"],
                    "content": content,
                }
            )
        return results

    async def on_message_appended(
        self, user_id: str, session_id: str, msg_id: str, content: str, dek: bytes
    ):
        vec = embed_texts([content]).astype(np.float32).reshape(1, -1)
        nonce, ct, alg = encrypt_vector(
            dek, user_id, msg_id, vec[0].tobytes(), session_id=session_id
        )
        await self.db.store_vector(msg_id, user_id, vec.shape[1], nonce, ct, alg)
        index = await self.registry.get_or_build(user_id, dek)
        index.add_batch([msg_id], vec)

    async def maintenance_tick(self) -> None:
        self.registry.evict_idle()
