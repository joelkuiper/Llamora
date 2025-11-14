"""Composable pipeline orchestration for search queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .candidate_generator import (
    BaseSearchCandidateGenerator,
    CandidateMap,
)
from .normalizer import BaseSearchNormalizer, NormalizedQuery
from .reranker import BaseSearchReranker
from .tag_enricher import BaseTagEnricher, TagEnrichment


@dataclass(slots=True)
class SearchPipelineComponents:
    """Concrete pipeline step implementations."""

    normalizer: BaseSearchNormalizer
    candidate_generator: BaseSearchCandidateGenerator
    tag_enricher: BaseTagEnricher
    reranker: BaseSearchReranker


@dataclass(slots=True)
class SearchPipelineResult:
    """Outcome of executing the search pipeline."""

    normalized: NormalizedQuery
    candidates: CandidateMap
    results: list[dict[str, Any]]
    enrichment: TagEnrichment

    @property
    def truncated(self) -> bool:
        """Return whether the original query required truncation."""

        return self.normalized.truncated


@dataclass(slots=True)
class SearchPipeline:
    """Execute the configured search pipeline components in order."""

    components: SearchPipelineComponents

    async def execute(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int,
        k2: int,
    ) -> SearchPipelineResult:
        """Normalize, expand, enrich, and rerank search results for ``query``."""

        comps = self.components

        normalized = comps.normalizer.normalize(user_id, query)
        candidate_map: CandidateMap = await comps.candidate_generator.generate(
            user_id,
            dek,
            normalized.text,
            k1,
            k2,
        )

        limit = max(k2, len(candidate_map), 1)
        enrichment = await comps.tag_enricher.enrich(
            user_id,
            dek,
            normalized.text,
            candidate_map,
            limit,
        )

        if not candidate_map:
            return SearchPipelineResult(
                normalized=normalized,
                candidates=candidate_map,
                results=[],
                enrichment=enrichment,
            )

        ordered_candidates = list(candidate_map.values())
        results = comps.reranker.rerank(
            normalized.text,
            ordered_candidates,
            k2,
            enrichment.boosts,
        )

        return SearchPipelineResult(
            normalized=normalized,
            candidates=candidate_map,
            results=results,
            enrichment=enrichment,
        )


__all__ = [
    "SearchPipeline",
    "SearchPipelineComponents",
    "SearchPipelineResult",
]
