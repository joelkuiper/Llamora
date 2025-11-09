import logging
import re
import time
from collections import OrderedDict
from typing import Any, Sequence, Tuple

import orjson

from llamora.settings import settings
from llamora.app.services.index_worker import IndexWorker
from llamora.app.services.vector_search import VectorSearchService
from llamora.app.services.lexical_reranker import LexicalReranker
from llamora.app.util.tags import canonicalize, display, tag_hash


TOKEN_PATTERN = re.compile(r"\S+")

logger = logging.getLogger(__name__)


class InvalidSearchQuery(ValueError):
    """Exception raised when a provided search query is invalid."""


IndexJob = Tuple[str, str, str, bytes]


class SearchAPI:
    """High level search interface operating on encrypted messages."""

    def __init__(
        self,
        db,
        vector_search: VectorSearchService | None = None,
        lexical_reranker: LexicalReranker | None = None,
    ):
        self.db = db
        self.vector_search = vector_search or VectorSearchService(db)
        self.lexical_reranker = lexical_reranker or LexicalReranker()
        self._index_worker = IndexWorker(
            self,
            max_queue_size=int(settings.WORKERS.index_worker.max_queue_size),
            batch_size=int(settings.WORKERS.index_worker.batch_size),
            flush_interval=float(settings.WORKERS.index_worker.flush_interval),
        )

    async def warm_index(self, user_id: str, dek: bytes) -> None:
        """Ensure the vector index for ``user_id`` is resident in memory."""

        start = time.perf_counter()
        logger.debug("Pre-warming vector index for user %s", user_id)
        try:
            await self.vector_search.index_store.ensure_index(user_id, dek)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Vector index warm-up failed for user %s", user_id)
            return

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Vector index warm-up completed for user %s in %.1fms",
            user_id,
            elapsed_ms,
        )

    async def start(self) -> None:
        """Start background services for the search API."""
        await self._index_worker.start()

    async def stop(self) -> None:
        """Stop background services for the search API."""
        await self._index_worker.stop()

    async def enqueue_index_job(
        self, user_id: str, message_id: str, plaintext: str, dek: bytes
    ) -> None:
        await self._index_worker.enqueue(user_id, message_id, plaintext, dek)

    async def bulk_index(self, jobs: Sequence[IndexJob]) -> None:
        if not jobs:
            return

        start = time.perf_counter()
        decode_fallbacks = 0
        parsed: list[IndexJob] = []
        for user_id, msg_id, plaintext, dek in jobs:
            content = plaintext
            try:
                record = orjson.loads(plaintext)
            except orjson.JSONDecodeError:
                decode_fallbacks += 1
                logger.debug(
                    "Failed to decode plaintext for message %s (user %s)",
                    msg_id,
                    user_id,
                )
            else:
                content = record.get("message", content)
            parsed.append((user_id, msg_id, content, dek))

        await self.vector_search.index_store.bulk_index(parsed)

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Bulk indexed %d messages for %d users in %.1fms (decode_fallbacks=%d, dropped=%d)",
            len(parsed),
            len({job[0] for job in parsed}),
            elapsed_ms,
            decode_fallbacks,
            self._index_worker.dropped_jobs,
        )

    def _normalize_query(self, user_id: str, query: str) -> tuple[str, bool]:
        """Return the normalized query text and whether truncation occurred."""

        normalized = (query or "").strip()
        if not normalized:
            logger.info("Rejecting empty search query for user %s", user_id)
            raise InvalidSearchQuery("Search query must not be empty")

        truncated = False
        max_query_length = int(settings.LIMITS.max_search_query_length)
        if len(normalized) > max_query_length:
            logger.info(
                "Truncating overlong search query (len=%d, limit=%d) for user %s",
                len(normalized),
                max_query_length,
                user_id,
            )
            normalized = normalized[:max_query_length]
            truncated = True

        return normalized, truncated

    def _tokenize_query(self, normalized: str) -> list[str]:
        """Return unique canonical tokens extracted from ``normalized``."""

        seen_tokens: set[str] = set()
        tokens: list[str] = []
        for raw in TOKEN_PATTERN.findall(normalized):
            token = raw.strip()
            if not token:
                continue
            try:
                canonical = canonicalize(token)
            except ValueError:
                continue
            canonical_lower = canonical.lower()
            if canonical_lower in seen_tokens:
                continue
            seen_tokens.add(canonical_lower)
            tokens.append(canonical)
        return tokens

    async def _hydrate_candidates(
        self,
        user_id: str,
        dek: bytes,
        candidate_map: OrderedDict[str, dict],
        tag_hashes: list[bytes],
        limit: int,
    ) -> None:
        """Ensure candidates referenced by ``tag_hashes`` are present."""

        if not tag_hashes:
            return

        tag_message_ids = await self.db.tags.get_recent_messages_for_tag_hashes(
            user_id, tag_hashes, limit=limit
        )
        if not tag_message_ids:
            return

        missing_ids = [mid for mid in tag_message_ids if mid not in candidate_map]
        if not missing_ids:
            return

        rows = await self.vector_search.index_store.hydrate_messages(
            user_id, missing_ids, dek
        )
        row_map = {row["id"]: row for row in rows}

        for mid in tag_message_ids:
            if mid in candidate_map:
                continue
            row = row_map.get(mid)
            if not row:
                continue
            candidate_map[mid] = {
                "id": row["id"],
                "created_at": row["created_at"],
                "role": row["role"],
                "content": row.get("message", ""),
                "cosine": 0.0,
            }

    async def _compute_tag_boosts(
        self,
        user_id: str,
        candidate_map: OrderedDict[str, dict],
        tag_hashes: list[bytes],
    ) -> dict[str, float]:
        """Compute tag-based boost multipliers for ``candidate_map``."""

        if not candidate_map or not tag_hashes:
            return {}

        message_ids = list(candidate_map.keys())
        if not message_ids:
            return {}

        match_counts = await self.db.tags.get_tag_match_counts(
            user_id, tag_hashes, message_ids
        )
        boosts: dict[str, float] = {}
        for mid, count in match_counts.items():
            if count > 0:
                boosts[mid] = 1.0 + 0.1 * (count - 1)
        return boosts

    def _select_visible_tags(
        self,
        tags: list[dict[str, Any]],
        max_visible: int = 3,
    ) -> tuple[list[dict[str, Any]], bool]:
        if not tags or max_visible <= 0:
            return [], bool(tags)

        indexes = list(range(len(tags)))
        selected = indexes[:max_visible]
        match_indexes = [idx for idx, tag in enumerate(tags) if tag.get("is_match")]

        if match_indexes:
            match_set = set(match_indexes)
            selected_set = set(selected)
            for idx in match_indexes:
                if idx in selected_set:
                    continue
                replaced = False
                for candidate in reversed(selected):
                    if candidate in match_set:
                        continue
                    selected.remove(candidate)
                    selected.append(idx)
                    selected_set.remove(candidate)
                    selected_set.add(idx)
                    replaced = True
                    break
                if not replaced:
                    # All selected entries are already matches; nothing to swap.
                    continue

        selected.sort()
        visible = [tags[idx] for idx in selected]
        return visible, len(tags) > len(visible)

    def _prepare_tags(
        self,
        raw_tags: list[dict[str, Any]],
        token_lookup: set[str],
        max_visible: int = 3,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        prepared: list[dict[str, Any]] = []
        for tag in raw_tags:
            name = str(tag.get("name") or "").strip()
            if not name:
                continue
            try:
                canonical = canonicalize(name)
            except ValueError:
                canonical = name.strip()
            display_name = display(canonical)
            normalized = canonical.lower()
            prepared.append(
                {
                    "name": display_name,
                    "hash": tag.get("hash"),
                    "is_match": normalized in token_lookup,
                }
            )

        visible, has_more = self._select_visible_tags(prepared, max_visible)
        return prepared, visible, has_more

    async def _attach_tags(
        self,
        user_id: str,
        dek: bytes,
        results: list[dict],
        tokens: list[str],
    ) -> None:
        if not results:
            return

        message_ids = [res.get("id") for res in results if res.get("id")]
        if not message_ids:
            return

        token_lookup = {token.lower() for token in tokens}
        tag_map = await self.db.tags.get_tags_for_messages(user_id, message_ids, dek)

        for res in results:
            msg_id = res.get("id")
            raw_tags = tag_map.get(msg_id, []) if msg_id else []
            prepared, visible, has_more = self._prepare_tags(raw_tags, token_lookup)
            res["tags"] = prepared
            res["visible_tags"] = visible
            res["has_more_tags"] = has_more

    async def search(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int = int(settings.SEARCH.progressive.k1),
        k2: int = int(settings.SEARCH.progressive.k2),
    ) -> tuple[str, list[dict], bool]:
        normalized, truncated = self._normalize_query(user_id, query)

        logger.debug("Search requested by user %s with k1=%d k2=%d", user_id, k1, k2)
        candidates = await self.vector_search.search_candidates(
            user_id, dek, normalized, k1, k2
        )

        candidate_map: OrderedDict[str, dict] = OrderedDict()
        for cand in candidates:
            mid = cand.get("id")
            if not mid:
                continue
            existing = candidate_map.get(mid)
            if existing is None or cand.get("cosine", 0.0) > existing.get(
                "cosine", 0.0
            ):
                candidate_map[mid] = cand

        tokens = self._tokenize_query(normalized)
        boosts: dict[str, float] = {}
        if tokens:
            tag_hashes = [tag_hash(user_id, t) for t in tokens]
            limit = max(k2, len(candidate_map), 1)
            await self._hydrate_candidates(
                user_id, dek, candidate_map, tag_hashes, limit
            )
            boosts = await self._compute_tag_boosts(user_id, candidate_map, tag_hashes)

        if not candidate_map:
            logger.debug(
                "No candidates found for user %s; returning empty result set", user_id
            )
            return normalized, [], truncated

        ordered_candidates = list(candidate_map.values())

        results = self.lexical_reranker.rerank(
            normalized, ordered_candidates, k2, boosts
        )
        await self._attach_tags(user_id, dek, results, tokens)
        logger.debug("Returning %d results for user %s", len(results), user_id)
        return normalized, results, truncated

    async def on_message_appended(
        self, user_id: str, msg_id: str, plaintext: str, dek: bytes
    ) -> None:
        await self.bulk_index([(user_id, msg_id, plaintext, dek)])

    async def maintenance_tick(self) -> None:
        await self.vector_search.maintenance_tick()
