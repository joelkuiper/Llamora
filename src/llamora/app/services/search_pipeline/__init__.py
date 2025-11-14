"""Search pipeline component interfaces and defaults."""
from __future__ import annotations

from .exceptions import InvalidSearchQuery
from .normalizer import BaseSearchNormalizer, DefaultSearchNormalizer, NormalizedQuery
from .candidate_generator import (
    BaseSearchCandidateGenerator,
    DefaultSearchCandidateGenerator,
)
from .tag_enricher import BaseTagEnricher, DefaultTagEnricher, TagEnrichment
from .reranker import BaseSearchReranker, DefaultSearchReranker
from .pipeline import (
    SearchPipeline,
    SearchPipelineComponents,
    SearchPipelineResult,
)

__all__ = [
    "InvalidSearchQuery",
    "BaseSearchNormalizer",
    "DefaultSearchNormalizer",
    "NormalizedQuery",
    "BaseSearchCandidateGenerator",
    "DefaultSearchCandidateGenerator",
    "BaseTagEnricher",
    "DefaultTagEnricher",
    "TagEnrichment",
    "BaseSearchReranker",
    "DefaultSearchReranker",
    "SearchPipeline",
    "SearchPipelineComponents",
    "SearchPipelineResult",
]
