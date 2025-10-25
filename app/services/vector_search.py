import logging
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
from app.embed.model import async_embed_texts
from app.index.message_ann import MessageIndexRegistry


logger = logging.getLogger(__name__)


class VectorSearchService:
    """Handles ANN index access and progressive warm-up for message search."""

    def __init__(self, db):
        self.db = db
        self.registry = MessageIndexRegistry(db)

    async def search_candidates(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int = PROGRESSIVE_K1,
        k2: int = PROGRESSIVE_K2,
    ) -> List[dict]:
        logger.debug(
            "Vector search requested by user %s with k1=%d k2=%d", user_id, k1, k2
        )
        index = await self.registry.get_or_build(user_id, dek)
        q_vec = (await async_embed_texts([query])).astype(np.float32).reshape(1, -1)

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
        logger.debug("Initial vector search returned %d candidates", len(ids))

        rounds = 0
        while not quality(ids, cosines):
            elapsed_ms = (time.monotonic() - start) * 1000
            if rounds >= PROGRESSIVE_ROUNDS or elapsed_ms >= PROGRESSIVE_MAX_MS:
                logger.debug(
                    "Stopping vector backfill after %d rounds (%.1fms)",
                    rounds,
                    elapsed_ms,
                )
                break
            added = await self.registry.expand_older(user_id, dek, PROGRESSIVE_BATCH)
            logger.debug("Backfill round %d added %d vectors", rounds + 1, added)
            if added <= 0:
                break
            rounds += 1
            if rounds == 1:
                current_k1 = min(2 * current_k1, 512)
            ids, dists = index.search(q_vec, current_k1)
            cosines = [1 - d for d in dists]

        seen = set()
        dedup_ids: List[str] = []
        id_cos: dict[str, float] = {}
        for mid, cos in zip(ids, cosines):
            if mid not in seen:
                seen.add(mid)
                dedup_ids.append(mid)
            if mid not in id_cos:
                id_cos[mid] = cos

        rows = await self.db.messages.get_messages_by_ids(user_id, dedup_ids, dek)
        row_map = {r["id"]: r for r in rows}

        results: List[dict] = []
        for mid in dedup_ids:
            row = row_map.get(mid)
            if not row:
                continue
            content = row.get("message", "")
            results.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "role": row["role"],
                    "content": content,
                    "cosine": id_cos.get(mid, 0.0),
                }
            )

        results.sort(key=lambda r: r["cosine"], reverse=True)
        logger.debug("Vector search returning %d hydrated candidates", len(results))
        return results

    async def append_message(
        self, user_id: str, message_id: str, content: str, dek: bytes
    ) -> None:
        logger.debug(
            "Adding message %s to vector index for user %s", message_id, user_id
        )
        vec = (await async_embed_texts([content])).astype(np.float32).reshape(1, -1)
        await self.db.vectors.store_vector(message_id, user_id, vec[0], dek)
        index = await self.registry.get_or_build(user_id, dek)
        if not index.contains(message_id):
            index.add_batch([message_id], vec)

    async def maintenance_tick(self) -> None:
        logger.debug("Running vector search maintenance")
        self.registry.evict_idle()
