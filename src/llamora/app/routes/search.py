import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from quart import Blueprint, Request, jsonify, render_template, request, stream_with_context
from llamora.app.api.search import InvalidSearchQuery
from llamora.app.services.container import get_search_api, get_services
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.chat_helpers import StreamSession, format_sse_event
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
    debug_delay_ms = float(getattr(settings.SEARCH, "stream_debug_delay_ms", 0))
    if debug_delay_ms > 0:
        # HACK: slow down search responses for visual inspection.
        await asyncio.sleep(debug_delay_ms / 1000.0)
    offset = _parse_offset(request.args.get("offset", 0))
    page_limit = context.page_size if offset > 0 else context.initial_page_size
    session_id = request.args.get("sid") or ""
    results: list = []
    truncation_notice: str | None = None
    sanitized_query = ""
    has_more = False
    next_offset = 0
    total_known = False
    showing_count = 0
    returned_session_id: str | None = None

    if context.query:
        _, user, dek = await require_user_and_dek()

        try:
            search_api = get_search_api()
            stream_result = await search_api.search_stream(
                user["id"],
                dek,
                context.query,
                session_id=session_id or None,
                offset=offset,
                page_limit=page_limit,
                result_window=context.result_window,
            )
            returned_session_id = stream_result.session_id
            sanitized_query = stream_result.normalized_query
            results = stream_result.results
            truncated = stream_result.truncated
            has_more = stream_result.has_more
            showing_count = stream_result.showing_count
            total_known = stream_result.total_known
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

    page_results = results
    next_offset = offset + len(page_results)

    logger.debug(
        "Route returning %d results (offset=%d, has_more=%s)",
        len(page_results),
        offset,
        has_more,
    )

    if offset > 0:
        if not page_results:
            return ""
        return await render_template(
            "partials/search_results_stream_chunk.html",
            results=page_results,
            has_more=has_more,
            next_offset=next_offset,
            session_id=returned_session_id,
        )

    return await render_template(
        "partials/search_results.html",
        results=page_results,
        has_query=bool(sanitized_query),
        truncation_notice=truncation_notice,
        total_known=total_known,
        showing_count=showing_count or next_offset,
        has_more=has_more,
        next_offset=next_offset,
        session_id=returned_session_id,
    )


def _format_sse_html(event_type: str, payload: str) -> str:
    text = (payload or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    data = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event_type}\n{data}\n\n"


@search_bp.get("/search/stream")
@login_required
async def search_stream():
    context = resolve_search_context(request)
    query = context.query
    if not query:
        async def _empty():
            yield _format_sse_html("initial", "")
            yield format_sse_event("done", {"showing": 0, "total_known": True})
        return StreamSession(stream_with_context(_empty)())

    _, user, dek = await require_user_and_dek()
    search_api = get_search_api()
    logger.debug("Search stream start user=%s query=%r", user["id"], query)

    async def _body():
        offset = 0
        page_limit = context.initial_page_size
        session_id: str | None = None
        truncation_notice: str | None = None

        try:
            while True:
                result = await search_api.search_stream(
                    user["id"],
                    dek,
                    query,
                    session_id=session_id,
                    offset=offset,
                    page_limit=page_limit,
                    result_window=context.result_window,
                )
                session_id = result.session_id
                if result.truncated and not truncation_notice:
                    truncation_notice = (
                        "Your search was truncated to the first "
                        f"{context.max_query_length} characters."
                    )

                if offset == 0 and result.normalized_query:
                    await get_services().db.search_history.record_search(
                        user["id"], result.normalized_query, dek
                    )

                if offset == 0:
                    html = await render_template(
                        "partials/search_results.html",
                        results=result.results,
                        has_query=bool(result.normalized_query),
                        truncation_notice=truncation_notice,
                        total_known=result.total_known,
                        showing_count=result.showing_count,
                    )
                    logger.debug(
                        "Search stream initial chunk user=%s items=%d has_more=%s",
                        user["id"],
                        len(result.results),
                        result.has_more,
                    )
                    yield _format_sse_html("initial", html)
                else:
                    if result.results:
                        html = await render_template(
                            "partials/search_results_stream_chunk.html",
                            results=result.results,
                        )
                        logger.debug(
                            "Search stream chunk user=%s offset=%d items=%d has_more=%s",
                            user["id"],
                            offset,
                            len(result.results),
                            result.has_more,
                        )
                        yield _format_sse_html("chunk", html)

                if not result.has_more or not result.results:
                    break

                offset += len(result.results)
                page_limit = context.page_size

        except InvalidSearchQuery:
            yield format_sse_event("error", "Invalid search query.")
            yield format_sse_event("done", {"showing": 0, "total_known": True})
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Search stream failed")
            yield format_sse_event("error", str(exc))
            yield format_sse_event("done", {"showing": 0, "total_known": True})
            return

        logger.debug(
            "Search stream done user=%s showing=%d total_known=%s",
            user["id"],
            result.showing_count,
            result.total_known,
        )
        yield format_sse_event(
            "done",
            {"showing": result.showing_count, "total_known": result.total_known},
        )

    return StreamSession(stream_with_context(_body)())


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
