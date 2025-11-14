from __future__ import annotations

"""Query normalization component for the search pipeline."""

import logging
from dataclasses import dataclass
from typing import Protocol

from llamora.app.services.search_config import SearchConfig

from .exceptions import InvalidSearchQuery

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NormalizedQuery:
    """Normalized representation of a user provided search query."""

    text: str
    truncated: bool


class BaseSearchNormalizer(Protocol):
    """Interface for query normalization components."""

    def normalize(self, user_id: str, query: str) -> NormalizedQuery:
        """Normalize ``query`` for ``user_id`` or raise :class:`InvalidSearchQuery`."""

        ...


class DefaultSearchNormalizer:
    """Normalize queries using the application search configuration limits."""

    def __init__(self, config: SearchConfig) -> None:
        self._config = config

    def normalize(self, user_id: str, query: str) -> NormalizedQuery:
        normalized = (query or "").strip()
        if not normalized:
            logger.info("Rejecting empty search query for user %s", user_id)
            raise InvalidSearchQuery("Search query must not be empty")

        truncated = False
        max_query_length = self._config.limits.max_search_query_length
        if len(normalized) > max_query_length:
            logger.info(
                "Truncating overlong search query (len=%d, limit=%d) for user %s",
                len(normalized),
                max_query_length,
                user_id,
            )
            normalized = normalized[:max_query_length]
            truncated = True

        return NormalizedQuery(text=normalized, truncated=truncated)


__all__ = ["NormalizedQuery", "BaseSearchNormalizer", "DefaultSearchNormalizer"]
