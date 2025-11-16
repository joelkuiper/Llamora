import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from quart import Blueprint, Request, jsonify, render_template, request
from llamora.app.api.search import InvalidSearchQuery
from llamora.app.services.container import get_search_api, get_services
from llamora.app.services.auth_helpers import login_required
from llamora.settings import settings
from llamora.app.util.frecency import (
    resolve_frecency_lambda,
    DEFAULT_FRECENCY_DECAY,
)
from llamora.app.routes.helpers import require_user_and_dek


logger = logging.getLogger(__name__)

search_bp = Blueprint("search", __name__)


FRECENT_TAG_LAMBDA = DEFAULT_FRECENCY_DECAY


@dataclass(slots=True)
class SearchContext:
    query: str
    decay_constant: float
    recent_limit: int
    max_query_length: int


def resolve_search_context(req: Request) -> SearchContext:
    sanitized_query = (req.args.get("q") or "").strip()
    lambda_param: Any = req.args.get("lambda")
    decay_constant = resolve_frecency_lambda(lambda_param, default=FRECENT_TAG_LAMBDA)
    max_query_length = int(settings.LIMITS.max_search_query_length)
    recent_limit = int(settings.SEARCH.recent_suggestion_limit)
    return SearchContext(
        query=sanitized_query,
        decay_constant=decay_constant,
        recent_limit=recent_limit,
        max_query_length=max_query_length,
    )


@search_bp.get("/search")
@login_required
async def search():
    context = resolve_search_context(request)
    logger.debug("Route search raw query='%s'", context.query)
    results: list = []
    truncation_notice: str | None = None
    sanitized_query = ""

    if context.query:
        _, user, dek = await require_user_and_dek()

        try:
            sanitized_query, results, truncated = await get_search_api().search(
                user["id"], dek, context.query
            )
        except InvalidSearchQuery:
            logger.info("Discarding invalid search query for user %s", user["id"])
            sanitized_query = ""
            results = []
            truncated = False

        if sanitized_query:
            await get_services().db.search_history.record_search(
                user["id"], sanitized_query, dek
            )

        if truncated:
            truncation_notice = (
                "Your search was truncated to the first "
                f"{context.max_query_length} characters."
            )

    logger.debug("Route returning %d results", len(results))
    return await render_template(
        "partials/search_results.html",
        results=results,
        has_query=bool(sanitized_query),
        truncation_notice=truncation_notice,
    )


@search_bp.get("/search/recent")
@login_required
async def recent_searches():
    context = resolve_search_context(request)
    _, user, dek = await require_user_and_dek()

    limit = context.recent_limit
    decay_constant = context.decay_constant
    history_repo = get_services().db.search_history
    tags_repo = get_services().db.tags

    recent_task = history_repo.get_recent_searches(user["id"], limit, dek)
    frecent_task = tags_repo.get_tag_frecency(user["id"], limit, decay_constant, dek)

    queries, frecent_rows = await asyncio.gather(recent_task, frecent_task)

    frecent_tags: list[str] = []
    seen_tags: set[str] = set()
    for row in frecent_rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen_tags:
            continue
        seen_tags.add(key)
        frecent_tags.append(name)

    return jsonify(
        {
            "recent": queries,
            "frecent_tags": frecent_tags,
            "lambda": decay_constant,
        }
    )
