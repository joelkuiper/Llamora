from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from ulid import ULID

from llamora.app.embed.model import async_embed_texts
from llamora.app.services.search_config import SearchConfig
from llamora.app.services.search_pipeline import SearchPipelineComponents
from llamora.app.services.vector_search import VectorSearchService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchStreamSession:
    session_id: str
    user_id: str
    normalized_query: str
    truncated: bool
    query_vec: np.ndarray
    candidate_map: OrderedDict[str, dict] = field(default_factory=OrderedDict)
    delivered_ids: set[str] = field(default_factory=set)
    current_k2: int = 0
    exhausted: bool = False
    last_access: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class SearchStreamResult:
    session_id: str
    normalized_query: str
    results: list[dict]
    truncated: bool
    has_more: bool
    showing_count: int
    total_known: bool


class SearchStreamManager:
    """Incremental search sessions with cached query vectors and candidates."""

    def __init__(
        self,
        *,
        vector_search: VectorSearchService,
        pipeline_components: SearchPipelineComponents,
        config: SearchConfig,
        stream_ttl: float,
        stream_max_sessions: int,
    ) -> None:
        self._vector_search = vector_search
        self._components = pipeline_components
        self._config = config
        self._stream_ttl = stream_ttl
        self._stream_max_sessions = stream_max_sessions
        self._sessions: dict[str, SearchStreamSession] = {}

    def _prune(self) -> None:
        if not self._sessions:
            return
        now = time.monotonic()
        stale = [
            sid
            for sid, session in self._sessions.items()
            if now - session.last_access > self._stream_ttl
        ]
        for sid in stale:
            self._sessions.pop(sid, None)
        if len(self._sessions) <= self._stream_max_sessions:
            return
        ordered = sorted(self._sessions.values(), key=lambda s: s.last_access)
        for session in ordered[: max(0, len(ordered) - self._stream_max_sessions)]:
            self._sessions.pop(session.session_id, None)

    async def fetch_page(
        self,
        *,
        user_id: str,
        dek: bytes,
        query: str,
        session_id: str | None,
        offset: int,
        page_limit: int,
        result_window: int,
        k1: int | None = None,
        k2: int | None = None,
    ) -> SearchStreamResult:
        """Fetch the next page without recomputing earlier candidates."""

        self._prune()
        normalized = self._components.normalizer.normalize(user_id, query)
        normalized_query = normalized.text
        truncated = normalized.truncated

        if offset == 0:
            session_id = None

        session: SearchStreamSession | None = None
        if session_id:
            session = self._sessions.get(session_id)
            if session and (
                session.user_id != user_id
                or session.normalized_query != normalized_query
            ):
                session = None

        if session is None:
            query_vec = (await async_embed_texts([normalized_query])).reshape(1, -1)
            session = SearchStreamSession(
                session_id=str(ULID()),
                user_id=user_id,
                normalized_query=normalized_query,
                truncated=truncated,
                query_vec=query_vec,
            )
            self._sessions[session.session_id] = session

        session.last_access = time.monotonic()

        cfg = self._config.progressive
        desired_limit = min(result_window, offset + page_limit)
        desired_k2 = max(int(cfg.k2), int(k2) if k2 is not None else 0, desired_limit)
        desired_k1 = max(int(cfg.k1), int(k1) if k1 is not None else 0, desired_k2)

        if desired_k2 > session.current_k2:
            candidates, total_count = await self._vector_search.search_candidates(
                user_id,
                dek,
                normalized_query,
                desired_k1,
                desired_k2,
                query_vec=session.query_vec,
                include_count=True,
            )
            if desired_k2 >= total_count:
                session.exhausted = True
            for candidate in candidates:
                message_id = candidate.get("id")
                if not message_id:
                    continue
                existing = session.candidate_map.get(message_id)
                if existing is None or candidate.get("cosine", 0.0) > existing.get(
                    "cosine",
                    0.0,
                ):
                    session.candidate_map[message_id] = candidate
            session.current_k2 = desired_k2

        limit = max(desired_k2, len(session.candidate_map), 1)
        enrichment = await self._components.tag_enricher.enrich(
            user_id,
            dek,
            normalized_query,
            session.candidate_map,
            limit,
        )

        ordered_candidates = list(session.candidate_map.values())
        reranked = self._components.reranker.rerank(
            normalized_query,
            ordered_candidates,
            limit=len(ordered_candidates),
            boosts=enrichment.boosts,
        )

        if offset > 0 and len(session.delivered_ids) != offset:
            session.delivered_ids.clear()
            for item in reranked[:offset]:
                session.delivered_ids.add(item["id"])

        page_results: list[dict] = []
        for item in reranked:
            if item["id"] in session.delivered_ids:
                continue
            page_results.append(item)
            session.delivered_ids.add(item["id"])
            if len(page_results) >= page_limit:
                break

        showing_count = len(session.delivered_ids)
        has_more = (
            not session.exhausted
            and len(session.candidate_map) >= desired_limit
            and desired_limit < result_window
            and len(page_results) > 0
            and len(page_results) >= page_limit
        )
        total_known = not has_more

        return SearchStreamResult(
            session_id=session.session_id,
            normalized_query=normalized_query,
            results=page_results,
            truncated=truncated,
            has_more=has_more,
            showing_count=showing_count,
            total_known=total_known,
        )


__all__ = ["SearchStreamManager", "SearchStreamResult"]
