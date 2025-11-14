from __future__ import annotations

"""Result reranking component for the search pipeline."""

from typing import Protocol, Sequence

from llamora.app.services.lexical_reranker import LexicalReranker


class BaseSearchReranker(Protocol):
    """Interface for producing the final ordered search results."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[dict],
        limit: int,
        boosts: dict[str, float],
    ) -> list[dict]:
        """Return the reranked result list."""


class DefaultSearchReranker:
    """Use the lexical reranker to score and order candidates."""

    def __init__(self, lexical_reranker: LexicalReranker | None = None) -> None:
        self._lexical_reranker = lexical_reranker or LexicalReranker()

    @property
    def lexical_reranker(self) -> LexicalReranker:
        """Expose the underlying :class:`LexicalReranker`."""

        return self._lexical_reranker

    def rerank(
        self,
        query: str,
        candidates: Sequence[dict],
        limit: int,
        boosts: dict[str, float],
    ) -> list[dict]:
        return self._lexical_reranker.rerank(query, candidates, limit, boosts)


__all__ = ["BaseSearchReranker", "DefaultSearchReranker"]
