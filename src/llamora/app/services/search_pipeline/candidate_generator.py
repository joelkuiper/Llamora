"""Candidate generation for the search pipeline."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Iterable, Protocol

from llamora.app.services.search_config import SearchConfig
from llamora.app.services.vector_search import VectorSearchService

logger = logging.getLogger(__name__)


Candidate = dict
CandidateMap = OrderedDict[str, Candidate]


class BaseSearchCandidateGenerator(Protocol):
    """Interface for producing ranked candidate messages."""

    async def generate(
        self,
        user_id: str,
        dek: bytes,
        normalized_query: str,
        k1: int,
        k2: int,
    ) -> CandidateMap:
        """Return candidate messages ordered by recency and vector distance."""

        ...


class DefaultSearchCandidateGenerator:
    """Use the configured :class:`VectorSearchService` to produce candidates."""

    def __init__(self, vector_search: VectorSearchService, config: SearchConfig) -> None:
        self._vector_search = vector_search
        self._config = config

    async def generate(
        self,
        user_id: str,
        dek: bytes,
        normalized_query: str,
        k1: int,
        k2: int,
    ) -> CandidateMap:
        logger.debug(
            "Generating search candidates for user %s with k1=%d k2=%d",
            user_id,
            k1,
            k2,
        )
        candidates = await self._vector_search.search_candidates(
            user_id,
            dek,
            normalized_query,
            k1,
            k2,
        )

        candidate_map: CandidateMap = OrderedDict()
        for candidate in candidates:
            message_id = candidate.get("id")
            if not message_id:
                continue
            existing = candidate_map.get(message_id)
            if existing is None or candidate.get("cosine", 0.0) > existing.get(
                "cosine",
                0.0,
            ):
                candidate_map[message_id] = candidate

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
