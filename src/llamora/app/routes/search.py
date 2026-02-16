import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from quart import Blueprint, Request, jsonify, render_template, request, abort
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


@search_bp.get("/search")
@login_required
async def search():
    context = resolve_search_context(request)
    logger.debug("Route search raw query='%s'", context.query)
    if request.args.get("offset"):
        abort(400, description="offset is not supported; use cursor")
    search_mode = (request.args.get("search_mode") or "").strip()
    cursor = (request.args.get("cursor") or "").strip()
    use_cursor = bool(cursor) and search_mode == "chunk"
    page_limit = context.page_size if use_cursor else context.initial_page_size
    session_id = cursor if use_cursor else ""
    results: list = []
    truncation_notice: str | None = None
    sanitized_query = ""
    has_more = False
    total_known = False
    showing_count = 0
    returned_session_id: str | None = None
    warming = False

    if context.query:
        _, user, dek = await require_user_and_dek()

        try:
            search_api = get_search_api()
            stream_result = await search_api.search_stream(
                user["id"],
                dek,
                context.query,
                session_id=session_id or None,
                page_limit=page_limit,
                result_window=context.result_window,
            )
            returned_session_id = stream_result.session_id
            if use_cursor and returned_session_id != cursor:
                return ""
            sanitized_query = stream_result.normalized_query
            results = stream_result.results
            truncated = stream_result.truncated
            has_more = stream_result.has_more
            showing_count = stream_result.showing_count
            total_known = stream_result.total_known
            warming = stream_result.warming
        except InvalidSearchQuery:
            logger.info("Discarding invalid search query for user %s", user["id"])
            sanitized_query = ""
            results = []
            truncated = False

        if sanitized_query and not use_cursor:
            await get_services().db.search_history.record_search(
                user["id"], sanitized_query, dek
            )

        if truncated:
            truncation_notice = (
                "Your search was truncated to the first "
                f"{context.max_query_length} characters."
            )

    page_results = results
    logger.debug(
        "Route returning %d results (has_more=%s)",
        len(page_results),
        has_more,
    )

    if use_cursor:
        if not page_results:
            return ""
        return await render_template(
            "components/search/search_results_chunk.html",
            results=page_results,
            has_more=has_more,
            session_id=returned_session_id,
        )

    return await render_template(
        "components/search/search_results.html",
        results=page_results,
        has_query=bool(sanitized_query),
        truncation_notice=truncation_notice,
        total_known=total_known,
        showing_count=showing_count,
        has_more=has_more,
        session_id=returned_session_id,
        warming=warming,
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
