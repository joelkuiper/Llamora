import logging
from dataclasses import dataclass
from typing import Any

from quart import Blueprint, Request, jsonify, render_template, request, abort
from llamora.app.api.search import InvalidSearchQuery
from llamora.app.services.container import get_search_api, get_services
from llamora.app.services.auth_helpers import login_required
from llamora.settings import settings
from llamora.app.routes.helpers import require_encryption_context
from llamora.app.util.tags import replace_emoji_shortcodes


logger = logging.getLogger(__name__)

search_bp = Blueprint("search", __name__)


@dataclass(slots=True)
class SearchContext:
    query: str
    recent_limit: int
    max_query_length: int
    page_size: int
    initial_page_size: int
    result_window: int


@dataclass(slots=True)
class SearchViewModel:
    results: list
    truncation_notice: str | None = None
    sanitized_query: str = ""
    has_more: bool = False
    total_known: bool = False
    showing_count: int = 0
    returned_session_id: str | None = None
    warming: bool = False
    index_coverage: dict[str, Any] | None = None


def _empty_search_view_model() -> SearchViewModel:
    return SearchViewModel(results=[])


def resolve_search_context(req: Request) -> SearchContext:
    sanitized_query = replace_emoji_shortcodes(req.args.get("q") or "").strip()
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
        recent_limit=recent_limit,
        max_query_length=max_query_length,
        page_size=page_size,
        initial_page_size=initial_page_size,
        result_window=result_window,
    )


async def _run_search(
    *,
    context: SearchContext,
    use_cursor: bool,
    cursor: str,
) -> SearchViewModel:
    model = _empty_search_view_model()
    if not context.query:
        return model

    _, user, ctx = await require_encryption_context()
    page_limit = context.page_size if use_cursor else context.initial_page_size

    try:
        search_api = get_search_api()
        stream_result = await search_api.search_stream(
            ctx,
            context.query,
            session_id=cursor or None,
            page_limit=page_limit,
            result_window=context.result_window,
        )
        model.returned_session_id = stream_result.session_id
        if use_cursor and model.returned_session_id != cursor:
            return model
        model.sanitized_query = stream_result.normalized_query
        model.results = stream_result.results
        model.has_more = stream_result.has_more
        model.showing_count = stream_result.showing_count
        model.total_known = stream_result.total_known
        model.warming = stream_result.warming
        model.index_coverage = stream_result.index_coverage
        if stream_result.truncated:
            model.truncation_notice = (
                "Your search was truncated to the first "
                f"{context.max_query_length} characters."
            )
    except InvalidSearchQuery:
        logger.info("Discarding invalid search query for user %s", user["id"])
        model.sanitized_query = ""
        model.results = []

    if model.sanitized_query and not use_cursor:
        await get_services().db.search_history.record_search(ctx, model.sanitized_query)
    return model


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
    vm = await _run_search(context=context, use_cursor=use_cursor, cursor=cursor)
    if use_cursor and vm.returned_session_id and vm.returned_session_id != cursor:
        return ""
    page_results = vm.results
    logger.debug(
        "Route returning %d results (has_more=%s)",
        len(page_results),
        vm.has_more,
    )

    if use_cursor:
        if not page_results:
            return ""
        return await render_template(
            "components/search/search_results_chunk.html",
            results=page_results,
            has_more=vm.has_more,
            session_id=vm.returned_session_id,
        )

    return await render_template(
        "components/search/search_results.html",
        results=page_results,
        has_query=bool(vm.sanitized_query),
        truncation_notice=vm.truncation_notice,
        total_known=vm.total_known,
        showing_count=vm.showing_count,
        has_more=vm.has_more,
        session_id=vm.returned_session_id,
        warming=vm.warming,
        index_coverage=vm.index_coverage,
    )


@search_bp.get("/search/recent")
@login_required
async def recent_searches():
    context = resolve_search_context(request)
    _, _user, ctx = await require_encryption_context()

    limit = context.recent_limit
    history_repo = get_services().db.search_history
    queries = await history_repo.get_recent_searches(ctx, limit)

    return jsonify(
        {
            "recent": queries,
        }
    )
