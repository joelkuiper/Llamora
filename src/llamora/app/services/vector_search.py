import logging
import time
from typing import Any, List, TYPE_CHECKING, overload, Literal

if TYPE_CHECKING:
    import numpy as np
else:
    np = Any  # type: ignore[assignment]

from llamora.app.embed.model import async_embed_texts
from llamora.app.index.entry_ann import EntryIndexStore
from llamora.app.services.search_config import SearchConfig


logger = logging.getLogger(__name__)


class VectorSearchService:
    """Handles ANN index access and progressive warm-up for entry search."""

    def __init__(self, db, config: SearchConfig, index_max_elements: int | None = None):
        self._config = config
        max_elements = index_max_elements or config.limits.entry_index_max_elements
        allow_growth = bool(config.limits.entry_index_allow_growth)
        self.index_store = EntryIndexStore(
            db,
            max_elements=max_elements,
            allow_growth=allow_growth,
        )

    def _quality_satisfied(self, ids: List[str], cosines: List[float], k2: int) -> bool:
        if len(ids) < k2:
            return False
        if not cosines:
            return False
        cfg = self._config.progressive
        max_cos = max(cosines)
        hits = sum(c >= float(cfg.poor_match_max_cos) for c in cosines)
        return max_cos >= float(cfg.poor_match_max_cos) and hits >= int(
            cfg.poor_match_min_hits
        )

    @staticmethod
    def _entry_id_from_vector_id(vector_id: str) -> str:
        if "::c" in vector_id:
            return vector_id.split("::c", 1)[0]
        return vector_id

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
        cfg = self._config.progressive
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

    @overload
    async def search_candidates(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int | None = None,
        k2: int | None = None,
        query_vec: "np.ndarray | None" = None,
        *,
        include_count: Literal[False] = False,
    ) -> List[dict[str, Any]]: ...

    @overload
    async def search_candidates(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int | None = None,
        k2: int | None = None,
        query_vec: "np.ndarray | None" = None,
        *,
        include_count: Literal[True] = True,
    ) -> tuple[List[dict[str, Any]], int]: ...

    async def search_candidates(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int | None = None,
        k2: int | None = None,
        query_vec: "np.ndarray | None" = None,
        *,
        include_count: bool = False,
    ) -> List[dict[str, Any]] | tuple[List[dict[str, Any]], int]:
        cfg = self._config.progressive
        k1 = int(k1) if k1 is not None else cfg.k1
        k2 = int(k2) if k2 is not None else cfg.k2
        logger.debug(
            "Vector search requested by user %s with k1=%d k2=%d", user_id, k1, k2
        )
        index = await self.index_store.ensure_index(user_id, dek)
        total_count = len(getattr(index, "entry_to_ids", {}))
        if query_vec is None:
            q_vec = (await async_embed_texts([query])).reshape(1, -1)
        else:
            q_vec = query_vec

        current_k1 = k1
        start = time.monotonic()
        ids, dists = index.search(q_vec, current_k1)
        cosines = (1.0 - dists).tolist()
        logger.debug("Initial vector search returned %d candidates", len(ids))

        rounds = 0
        while self._should_continue(start, rounds, ids, cosines, k2):
            added = await self.index_store.expand_older(
                user_id, dek, int(cfg.batch_size)
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
        for vector_id, cos in zip(ids, cosines):
            if vector_id is None:
                continue
            entry_id = self._entry_id_from_vector_id(vector_id)
            if entry_id not in seen:
                seen.add(entry_id)
                dedup_ids.append(entry_id)
            existing = id_cos.get(entry_id)
            if existing is None or cos > existing:
                id_cos[entry_id] = cos

        rows = await self.index_store.hydrate_entries(user_id, dedup_ids, dek)
        row_map = {r["id"]: r for r in rows}

        results: List[dict[str, Any]] = []
        for entry_id in dedup_ids:
            row = row_map.get(entry_id)
            if not row:
                continue
            content = row.get("text", "")
            results.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "created_date": row.get("created_date"),
                    "role": row["role"],
                    "content": content,
                    "cosine": id_cos.get(entry_id, 0.0),
                }
            )

        results.sort(key=lambda r: r["cosine"], reverse=True)
        logger.debug("Vector search returning %d hydrated candidates", len(results))
        if include_count:
            return results, total_count
        return results

    async def append_entry(
        self, user_id: str, entry_id: str, content: str, dek: bytes
    ) -> None:
        logger.debug("Adding entry %s to vector index for user %s", entry_id, user_id)
        await self.index_store.index_entry(user_id, entry_id, content, dek)

    async def maintenance_tick(self) -> None:
        logger.debug("Running vector search maintenance")
        await self.index_store.maintenance()
