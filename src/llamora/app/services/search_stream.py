from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from ulid import ULID

from llamora.app.embed.model import async_embed_texts
from llamora.app.services.crypto import CryptoContext
from llamora.app.services.search_config import SearchConfig
from llamora.app.services.search_pipeline import SearchPipelineComponents
from llamora.app.services.service_pulse import ServicePulse
from llamora.app.services.tag_service import TagService
from llamora.app.services.vector_search import VectorSearchService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchStreamSession:
    session_id: str
    user_id: str
    normalized_query: str
    truncated: bool
    query_vec: np.ndarray
    candidate_map: OrderedDict[str, Candidate] = field(default_factory=OrderedDict)
    delivered_ids: set[str] = field(default_factory=set)
    current_k2: int = 0
    exhausted: bool = False
    last_access: float = field(default_factory=time.monotonic)

    def estimated_memory_bytes(self) -> int:
        query_vec_bytes = int(getattr(self.query_vec, "nbytes", 0))
        candidate_payload_bytes = 0
        for entry_id, candidate in self.candidate_map.items():
            candidate_payload_bytes += len(entry_id) + 96
            candidate_payload_bytes += len(str(candidate.get("content", "")))
        delivered_bytes = sum(len(entry_id) + 32 for entry_id in self.delivered_ids)
        return query_vec_bytes + candidate_payload_bytes + delivered_bytes + 256

    def priority_score(self) -> int:
        return len(self.delivered_ids) + len(self.candidate_map) + self.current_k2


@dataclass(slots=True)
class SearchStreamResult:
    session_id: str
    normalized_query: str
    results: list[Candidate]
    truncated: bool
    has_more: bool
    showing_count: int
    total_known: bool
    warming: bool = False
    index_coverage: dict[str, float | int | str] | None = None


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
        tag_service: TagService,
        stream_global_memory_budget_bytes: int,
        service_pulse: ServicePulse | None = None,
    ) -> None:
        self._vector_search = vector_search
        self._components = pipeline_components
        self._config = config
        self._stream_ttl = stream_ttl
        self._stream_max_sessions = stream_max_sessions
        self._tag_service = tag_service
        self._stream_global_memory_budget_bytes = max(
            int(stream_global_memory_budget_bytes), 0
        )
        self._service_pulse = service_pulse
        self._sessions: dict[str, SearchStreamSession] = {}

    def _emit_budget_pressure(self, *, evicted: int, total_bytes: int) -> None:
        if self._service_pulse is None:
            return
        budget = self._stream_global_memory_budget_bytes
        pressure = (total_bytes / budget) if budget > 0 else 0.0
        payload = {
            "budget_bytes": budget,
            "total_bytes": total_bytes,
            "pressure": pressure,
            "evicted_sessions": evicted,
            "active_sessions": len(self._sessions),
        }
        try:
            self._service_pulse.emit("search.stream_budget", payload)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to emit stream budget pulse")

    def _estimated_total_memory_bytes(self) -> int:
        return sum(
            session.estimated_memory_bytes() for session in self._sessions.values()
        )

    def _enforce_memory_budget(self) -> None:
        budget = self._stream_global_memory_budget_bytes
        if budget <= 0 or not self._sessions:
            return
        total_bytes = self._estimated_total_memory_bytes()
        evicted = 0
        if total_bytes > budget:
            ordered = sorted(
                self._sessions.values(),
                key=lambda session: (session.priority_score(), session.last_access),
            )
            for session in ordered:
                if total_bytes <= budget:
                    break
                total_bytes = max(total_bytes - session.estimated_memory_bytes(), 0)
                self._sessions.pop(session.session_id, None)
                evicted += 1
        self._emit_budget_pressure(evicted=evicted, total_bytes=total_bytes)

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
        self._enforce_memory_budget()
        if len(self._sessions) <= self._stream_max_sessions:
            return
        ordered = sorted(self._sessions.values(), key=lambda s: s.last_access)
        for session in ordered[: max(0, len(ordered) - self._stream_max_sessions)]:
            self._sessions.pop(session.session_id, None)

    async def fetch_page(
        self,
        *,
        ctx: CryptoContext,
        query: str,
        session_id: str | None,
        page_limit: int,
        result_window: int,
        k1: int | None = None,
        k2: int | None = None,
    ) -> SearchStreamResult:
        """Fetch the next page without recomputing earlier candidates."""

        self._prune()
        user_id = ctx.user_id
        normalized = self._components.normalizer.normalize(user_id, query)
        normalized_query = normalized.text
        truncated = normalized.truncated

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
        delivered = len(session.delivered_ids)
        desired_limit = min(result_window, delivered + page_limit)
        desired_k2 = max(int(cfg.k2), int(k2) if k2 is not None else 0, desired_limit)
        desired_k1 = max(int(cfg.k1), int(k1) if k1 is not None else 0, desired_k2)

        index_coverage: dict[str, float | int | str] | None = None
        if desired_k2 > session.current_k2:
            if self._config.include_index_coverage_hints:
                (
                    candidates,
                    total_count,
                    index_coverage,
                ) = await self._vector_search.search_candidates(
                    ctx,
                    normalized_query,
                    desired_k1,
                    desired_k2,
                    query_vec=session.query_vec,
                    include_count=True,
                    include_coverage=True,
                )
            else:
                candidates, total_count = await self._vector_search.search_candidates(
                    ctx,
                    normalized_query,
                    desired_k1,
                    desired_k2,
                    query_vec=session.query_vec,
                    include_count=True,
                )
            if desired_k2 >= total_count:
                session.exhausted = True
            for candidate in candidates:
                entry_id = candidate.get("id")
                if not entry_id:
                    continue
                existing = session.candidate_map.get(entry_id)
                if existing is None or candidate.get("cosine", 0.0) > existing.get(
                    "cosine",
                    0.0,
                ):
                    session.candidate_map[entry_id] = candidate
            session.current_k2 = desired_k2

        limit = max(desired_k2, len(session.candidate_map), 1)
        enrichment = await self._components.tag_enricher.enrich(
            ctx,
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

        page_results: list[dict] = []
        for item in reranked:
            if item["id"] in session.delivered_ids:
                continue
            page_results.append(item)
            session.delivered_ids.add(item["id"])
            if len(page_results) >= page_limit:
                break

        if page_results:
            await self._tag_service.hydrate_search_results(
                ctx,
                page_results,
                enrichment.tokens,
            )

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
            warming=self._vector_search.index_store.is_warming(user_id),
            index_coverage=index_coverage,
        )


__all__ = ["SearchStreamManager", "SearchStreamResult"]
Candidate = dict[str, Any]
