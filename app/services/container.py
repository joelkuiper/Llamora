"""Application service container helpers."""

from __future__ import annotations

from dataclasses import dataclass
from quart import current_app

from db import LocalDB
from app.api.search import SearchAPI
from app.services.lexical_reranker import LexicalReranker
from app.services.vector_search import VectorSearchService


@dataclass(slots=True)
class AppServices:
    """Bundle long-lived application services."""

    db: LocalDB
    vector_search: VectorSearchService
    lexical_reranker: LexicalReranker
    search_api: SearchAPI

    @classmethod
    def create(cls) -> "AppServices":
        db = LocalDB()
        vector_search = VectorSearchService(db)
        lexical_reranker = LexicalReranker()
        search_api = SearchAPI(db, vector_search, lexical_reranker)
        db.set_search_api(search_api)
        return cls(
            db=db,
            vector_search=vector_search,
            lexical_reranker=lexical_reranker,
            search_api=search_api,
        )


def get_services() -> AppServices:
    """Return the lazily initialised :class:`AppServices` container."""

    services = current_app.extensions.get("llamora")
    if services is None:
        raise RuntimeError("App services container is not initialised")
    return services


def get_db() -> LocalDB:
    """Convenience accessor for the application database."""

    return get_services().db


def get_search_api() -> SearchAPI:
    """Convenience accessor for the search API service."""

    return get_services().search_api
