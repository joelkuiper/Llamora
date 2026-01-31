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
    page_size: int
    initial_page_size: int
    result_window: int


def resolve_search_context(req: Request) -> SearchContext:
    sanitized_query = (req.args.get("q") or "").strip()
    lambda_param: Any = req.args.get("lambda")
    decay_constant = resolve_frecency_lambda(lambda_param, default=FRECENT_TAG_LAMBDA)
    max_query_length = int(settings.LIMITS.max_search_query_length)
    recent_limit = int(settings.SEARCH.recent_suggestion_limit)
    page_size = max(1, int(getattr(settings.SEARCH, "page_size", 40)))
    initial_page_size = max(
        page_size, int(getattr(settings.SEARCH, "initial_page_size", page_size))
    )
    result_window = max(
        initial_page_size, int(getattr(settings.SEARCH, "result_window", 100))
    )
    return SearchContext(
        query=sanitized_query,
        decay_constant=decay_constant,
        recent_limit=recent_limit,
        max_query_length=max_query_length,
        page_size=page_size,
        initial_page_size=initial_page_size,
        result_window=result_window,
    )


def _parse_offset(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


@search_bp.get("/search")
@login_required
async def search():
    context = resolve_search_context(request)
    logger.debug("Route search raw query='%s'", context.query)
    offset = _parse_offset(request.args.get("offset", 0))
    page_limit = context.page_size if offset > 0 else context.initial_page_size
    results: list = []
    truncation_notice: str | None = None
    sanitized_query = ""
    has_more = False
    next_offset = 0
    total_count = 0

    if context.query:
        _, user, dek = await require_user_and_dek()

        try:
            search_api = get_search_api()
            cfg = search_api.search_config.progressive
            desired_k2 = max(
                int(cfg.k2),
                context.result_window,
                page_limit + offset,
            )
            desired_k1 = max(int(cfg.k1), desired_k2)
            sanitized_query, results, truncated = await search_api.search(
                user["id"],
                dek,
                context.query,
                k1=desired_k1,
                k2=desired_k2,
            )
        except InvalidSearchQuery:
            logger.info("Discarding invalid search query for user %s", user["id"])
            sanitized_query = ""
            results = []
            truncated = False

        if sanitized_query and offset == 0:
            await get_services().db.search_history.record_search(
                user["id"], sanitized_query, dek
            )

        if truncated:
            truncation_notice = (
                "Your search was truncated to the first "
                f"{context.max_query_length} characters."
            )

    total_count = len(results)
    page_results = results[offset : offset + page_limit]
    next_offset = offset + len(page_results)
    has_more = next_offset < total_count

    logger.debug(
        "Route returning %d results (offset=%d, total=%d, has_more=%s)",
        len(page_results),
        offset,
        total_count,
        has_more,
    )

    if offset > 0:
        if not page_results:
            return ""
        return await render_template(
            "partials/search_results_chunk.html",
            results=page_results,
            has_more=has_more,
            next_offset=next_offset,
        )

    return await render_template(
        "partials/search_results.html",
        results=page_results,
        has_query=bool(sanitized_query),
        truncation_notice=truncation_notice,
        total_count=total_count,
        has_more=has_more,
        next_offset=next_offset,
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
