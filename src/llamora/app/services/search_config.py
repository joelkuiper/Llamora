from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ProgressiveSearchConfig:
    """Configuration for progressive vector search expansion."""

    k1: int
    k2: int
    rounds: int
    batch_size: int
    max_ms: float
    poor_match_max_cos: float
    poor_match_min_hits: int

    def as_dict(self) -> dict[str, Any]:
        """Return the configuration as a plain dictionary."""

        return asdict(self)


@dataclass(slots=True, frozen=True)
class SearchLimits:
    """Limits applied to entry search operations."""

    recent_limit: int
    recent_suggestion_limit: int
    entry_index_max_elements: int
    entry_index_allow_growth: bool
    max_search_query_length: int

    def as_dict(self) -> dict[str, Any]:
        """Return the limits as a plain dictionary."""

        return asdict(self)


@dataclass(slots=True, frozen=True)
class SearchConfig:
    """Aggregate search configuration used across services."""

    progressive: ProgressiveSearchConfig
    limits: SearchLimits
    embedding_global_memory_budget_bytes: int
    stream_global_memory_budget_bytes: int
    progressive_inline_backfill: bool
    include_index_coverage_hints: bool

    @classmethod
    def from_settings(cls, settings: Any) -> "SearchConfig":
        """Construct a :class:`SearchConfig` from application settings."""

        search_settings = settings.SEARCH
        progressive_settings = search_settings.progressive
        limits = SearchLimits(
            recent_limit=int(search_settings.recent_limit),
            recent_suggestion_limit=int(search_settings.recent_suggestion_limit),
            entry_index_max_elements=int(search_settings.entry_index_max_elements),
            entry_index_allow_growth=bool(
                getattr(search_settings, "entry_index_allow_growth", False)
            ),
            max_search_query_length=int(settings.LIMITS.max_search_query_length),
        )
        progressive = ProgressiveSearchConfig(
            k1=int(progressive_settings.k1),
            k2=int(progressive_settings.k2),
            rounds=int(progressive_settings.rounds),
            batch_size=int(progressive_settings.batch_size),
            max_ms=float(progressive_settings.max_ms),
            poor_match_max_cos=float(progressive_settings.poor_match_max_cos),
            poor_match_min_hits=int(progressive_settings.poor_match_min_hits),
        )
        return cls(
            progressive=progressive,
            limits=limits,
            embedding_global_memory_budget_bytes=int(
                getattr(settings.EMBEDDING, "global_memory_budget_bytes", 0)
            ),
            stream_global_memory_budget_bytes=int(
                getattr(search_settings, "stream_global_memory_budget_bytes", 0)
            ),
            progressive_inline_backfill=bool(
                getattr(search_settings, "progressive_inline_backfill", True)
            ),
            include_index_coverage_hints=bool(
                getattr(search_settings, "include_index_coverage_hints", False)
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the full configuration as a dictionary."""

        return {
            "progressive": self.progressive.as_dict(),
            "limits": self.limits.as_dict(),
            "embedding_global_memory_budget_bytes": self.embedding_global_memory_budget_bytes,
            "stream_global_memory_budget_bytes": self.stream_global_memory_budget_bytes,
            "progressive_inline_backfill": self.progressive_inline_backfill,
            "include_index_coverage_hints": self.include_index_coverage_hints,
        }


__all__ = [
    "ProgressiveSearchConfig",
    "SearchConfig",
    "SearchLimits",
]
