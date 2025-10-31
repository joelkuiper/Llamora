import logging
import time
from typing import List

from llamora.settings import settings
from llamora.app.embed.model import async_embed_texts
from llamora.app.index.message_ann import MessageIndexStore


logger = logging.getLogger(__name__)


class VectorSearchService:
    """Handles ANN index access and progressive warm-up for message search."""

    def __init__(self, db, index_max_elements: int | None = None):
        max_elements = index_max_elements or int(
            settings.SEARCH.message_index_max_elements
        )
        self.index_store = MessageIndexStore(db, max_elements=max_elements)

    def _quality_satisfied(self, ids: List[str], cosines: List[float], k2: int) -> bool:
        if len(ids) < k2:
            return False
        if not cosines:
            return False
        cfg = settings.SEARCH.progressive
        max_cos = max(cosines)
        hits = sum(c >= float(cfg.poor_match_max_cos) for c in cosines)
        return max_cos >= float(cfg.poor_match_max_cos) and hits >= int(
            cfg.poor_match_min_hits
        )

    def _should_continue(
        self,
        start: float,
        rounds: int,
        ids: List[str],
        cosines: List[float],
        k2: int,
    ) -> bool:
        if self._quality_satisfied(ids, cosines, k2):
            return False
        cfg = settings.SEARCH.progressive
        if rounds >= int(cfg.rounds):
            return False
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms >= float(cfg.max_ms):
            logger.debug(
                "Stopping vector backfill after %d rounds due to time budget (%.1fms)",
                rounds,
                elapsed_ms,
            )
            return False
        return True

    async def search_candidates(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int = int(settings.SEARCH.progressive.k1),
        k2: int = int(settings.SEARCH.progressive.k2),
    ) -> List[dict]:
        logger.debug(
            "Vector search requested by user %s with k1=%d k2=%d", user_id, k1, k2
        )
        index = await self.index_store.ensure_index(user_id, dek)
        q_vec = (await async_embed_texts([query])).reshape(1, -1)

        current_k1 = k1
        start = time.monotonic()
        ids, dists = index.search(q_vec, current_k1)
        cosines = (1.0 - dists).tolist()
        logger.debug("Initial vector search returned %d candidates", len(ids))

        rounds = 0
        while self._should_continue(start, rounds, ids, cosines, k2):
            added = await self.index_store.expand_older(
                user_id, dek, int(settings.SEARCH.progressive.batch_size)
            )
            logger.debug("Backfill round %d added %d vectors", rounds + 1, added)
            if added <= 0:
                break
            rounds += 1
            if rounds == 1:
                current_k1 = min(2 * current_k1, 512)
            ids, dists = index.search(q_vec, current_k1)
            cosines = (1.0 - dists).tolist()

        seen = set()
        dedup_ids: List[str] = []
        id_cos: dict[str, float] = {}
        for mid, cos in zip(ids, cosines):
            if mid is None:
                continue
            if mid not in seen:
                seen.add(mid)
                dedup_ids.append(mid)
            existing = id_cos.get(mid)
            if existing is None or cos > existing:
                id_cos[mid] = cos

        rows = await self.index_store.hydrate_messages(user_id, dedup_ids, dek)
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
        await self.index_store.index_message(user_id, message_id, content, dek)

    async def maintenance_tick(self) -> None:
        logger.debug("Running vector search maintenance")
        await self.index_store.maintenance()
