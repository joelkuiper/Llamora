"""Candidate generation for the search pipeline."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Iterable, Protocol

from llamora.app.services.search_config import SearchConfig
from llamora.app.services.crypto import CryptoContext
from llamora.app.services.vector_search import VectorSearchService

logger = logging.getLogger(__name__)


Candidate = dict[str, Any]
CandidateMap = OrderedDict[str, Candidate]


class BaseSearchCandidateGenerator(Protocol):
    """Interface for producing ranked candidate entries."""

    async def generate(
        self,
        ctx: CryptoContext,
        normalized_query: str,
        k1: int,
        k2: int,
    ) -> CandidateMap:
        """Return candidate entries ordered by recency and vector distance."""

        ...


class DefaultSearchCandidateGenerator:
    """Use the configured :class:`VectorSearchService` to produce candidates."""

    def __init__(
        self, vector_search: VectorSearchService, config: SearchConfig
    ) -> None:
        self._vector_search = vector_search
        self._config = config

    async def generate(
        self,
        ctx: CryptoContext,
        normalized_query: str,
        k1: int,
        k2: int,
    ) -> CandidateMap:
        logger.debug(
            "Generating search candidates for user %s with k1=%d k2=%d",
            ctx.user_id,
            k1,
            k2,
        )
        candidates = await self._vector_search.search_candidates(
            ctx,
            normalized_query,
            k1,
            k2,
        )

        candidate_map: CandidateMap = OrderedDict()
        for candidate in candidates:
            entry_id = candidate.get("id")
            if not entry_id:
                continue
            existing = candidate_map.get(entry_id)
            if existing is None or candidate.get("cosine", 0.0) > existing.get(
                "cosine",
                0.0,
            ):
                candidate_map[entry_id] = candidate

        return candidate_map


def iter_candidates(candidate_map: CandidateMap) -> Iterable[Candidate]:
    """Yield candidate dictionaries in rank order."""

    return candidate_map.values()


__all__ = [
    "Candidate",
    "CandidateMap",
    "BaseSearchCandidateGenerator",
    "DefaultSearchCandidateGenerator",
    "iter_candidates",
]
