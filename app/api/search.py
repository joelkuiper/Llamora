import hashlib
import logging
import re

import orjson

from config import (
    INDEX_WORKER_MAX_QUEUE_SIZE,
    MAX_SEARCH_QUERY_LENGTH,
    MAX_TAG_LENGTH,
    PROGRESSIVE_K1,
    PROGRESSIVE_K2,
)
from app.services.index_worker import IndexWorker
from app.services.lexical_reranker import LexicalReranker
from app.services.vector_search import VectorSearchService


TOKEN_PATTERN = re.compile(r"\S+")

logger = logging.getLogger(__name__)


class InvalidSearchQuery(ValueError):
    """Exception raised when a provided search query is invalid."""


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
            self, max_queue_size=INDEX_WORKER_MAX_QUEUE_SIZE
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

    async def search(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int = PROGRESSIVE_K1,
        k2: int = PROGRESSIVE_K2,
    ) -> tuple[str, list[dict], bool]:
        normalized = (query or "").strip()
        if not normalized:
            logger.info("Rejecting empty search query for user %s", user_id)
            raise InvalidSearchQuery("Search query must not be empty")

        truncated = False
        if len(normalized) > MAX_SEARCH_QUERY_LENGTH:
            logger.info(
                "Truncating overlong search query (len=%d, limit=%d) for user %s",
                len(normalized),
                MAX_SEARCH_QUERY_LENGTH,
                user_id,
            )
            normalized = normalized[:MAX_SEARCH_QUERY_LENGTH]
            truncated = True

        logger.debug("Search requested by user %s with k1=%d k2=%d", user_id, k1, k2)
        candidates = await self.vector_search.search_candidates(
            user_id, dek, normalized, k1, k2
        )

        if not candidates:
            logger.debug(
                "No candidates found for user %s; returning empty result set", user_id
            )
            return normalized, [], truncated

        seen_tokens: set[str] = set()
        tokens: list[str] = []
        for raw in TOKEN_PATTERN.findall(normalized):
            token = raw.strip()
            if not token:
                continue
            if token.startswith("#"):
                token = "#" + token.lstrip("#")
            token = token[:MAX_TAG_LENGTH]
            if not token or token in seen_tokens:
                continue
            seen_tokens.add(token)
            tokens.append(token)
        boosts: dict[str, float] = {}
        if tokens:
            tag_hashes = [
                hashlib.sha256(f"{user_id}:{t}".encode("utf-8")).digest()
                for t in tokens
            ]
            message_ids = [c["id"] for c in candidates]
            tag_map = await self.db.tags.get_messages_with_tag_hashes(
                user_id, tag_hashes, message_ids
            )
            for mid, hashes in tag_map.items():
                boosts[mid] = 0.1 * len(hashes)

        results = self.lexical_reranker.rerank(normalized, candidates, k2, boosts)
        logger.debug("Returning %d results for user %s", len(results), user_id)
        return normalized, results, truncated

    async def on_message_appended(
        self, user_id: str, msg_id: str, plaintext: str, dek: bytes
    ) -> None:
        try:
            record = orjson.loads(plaintext)
            content = record.get("message", "")
        except orjson.JSONDecodeError:
            logger.debug(
                "Failed to decode plaintext for message %s (user %s)", msg_id, user_id
            )
            content = plaintext
        await self.vector_search.append_message(user_id, msg_id, content, dek)

    async def maintenance_tick(self) -> None:
        await self.vector_search.maintenance_tick()
