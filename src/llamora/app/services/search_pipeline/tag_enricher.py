"""Tag-based enrichment for search candidates."""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from llamora.app.services.tag_service import TagService
from llamora.app.util.tags import tag_hash
from llamora.persistence.local_db import LocalDB

from ..vector_search import VectorSearchService

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"\S+")


@dataclass(slots=True)
class TagEnrichment:
    """Result of tag enrichment for a set of candidates."""

    tokens: list[str]
    boosts: dict[str, float]


class BaseTagEnricher(Protocol):
    """Interface for enriching candidates with tag information."""

    async def enrich(
        self,
        user_id: str,
        dek: bytes,
        normalized_query: str,
        candidate_map: OrderedDict[str, dict],
        limit: int,
    ) -> TagEnrichment:
        """Augment ``candidate_map`` and compute tag boost weights."""

        ...


class DefaultTagEnricher:
    """Apply canonical tag matching and hydration to the candidate set."""

    def __init__(
        self,
        db: LocalDB,
        tag_service: TagService,
        vector_search: VectorSearchService,
    ) -> None:
        self._db = db
        self._tag_service = tag_service
        self._vector_search = vector_search

    async def enrich(
        self,
        user_id: str,
        dek: bytes,
        normalized_query: str,
        candidate_map: OrderedDict[str, dict],
        limit: int,
    ) -> TagEnrichment:
        tokens = self._tokenize(normalized_query)
        boosts: dict[str, float] = {}
        if not tokens:
            return TagEnrichment(tokens=tokens, boosts=boosts)

        tag_hashes = [tag_hash(user_id, token) for token in tokens]
        await self._hydrate_candidates(user_id, dek, candidate_map, tag_hashes, limit)
        boosts = await self._compute_tag_boosts(user_id, candidate_map, tag_hashes)
        return TagEnrichment(tokens=tokens, boosts=boosts)

    def _tokenize(self, normalized_query: str) -> list[str]:
        seen_tokens: set[str] = set()
        tokens: list[str] = []
        for raw_token in _TOKEN_PATTERN.findall(normalized_query):
            token = raw_token.strip()
            if not token:
                continue
            try:
                canonical = self._tag_service.canonicalize(token)
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
        if not tag_hashes:
            return

        tag_entry_ids = await self._db.tags.get_recent_entries_for_tag_hashes(
            user_id,
            tag_hashes,
            limit=limit,
        )
        if not tag_entry_ids:
            return

        missing_ids = [eid for eid in tag_entry_ids if eid not in candidate_map]
        if not missing_ids:
            return

        rows = await self._vector_search.index_store.hydrate_entries(
            user_id,
            missing_ids,
            dek,
        )
        row_map = {row["id"]: row for row in rows}
        for entry_id in tag_entry_ids:
            if entry_id in candidate_map:
                continue
            row = row_map.get(entry_id)
            if not row:
                continue
            candidate_map[entry_id] = {
                "id": row["id"],
                "created_at": row["created_at"],
                "created_date": row.get("created_date"),
                "role": row["role"],
                "content": row.get("text", ""),
                "cosine": 0.0,
            }

    async def _compute_tag_boosts(
        self,
        user_id: str,
        candidate_map: OrderedDict[str, dict],
        tag_hashes: list[bytes],
    ) -> dict[str, float]:
        if not candidate_map or not tag_hashes:
            return {}

        entry_ids = list(candidate_map.keys())
        if not entry_ids:
            return {}

        match_counts = await self._db.tags.get_tag_match_counts(
            user_id,
            tag_hashes,
            entry_ids,
        )
        boosts: dict[str, float] = {}
        for entry_id, count in match_counts.items():
            if count > 0:
                boosts[entry_id] = 1.0 + 0.1 * (count - 1)
        return boosts


__all__ = ["TagEnrichment", "BaseTagEnricher", "DefaultTagEnricher"]
