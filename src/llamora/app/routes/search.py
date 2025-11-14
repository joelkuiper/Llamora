import asyncio
import logging
from typing import Any

from quart import Blueprint, render_template, request, abort, jsonify
from llamora.app.api.search import InvalidSearchQuery
from llamora.app.services.container import get_search_api, get_services
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.session_context import get_session_context
from llamora.settings import settings
from llamora.app.util.frecency import (
    resolve_frecency_lambda,
    DEFAULT_FRECENCY_DECAY,
)


logger = logging.getLogger(__name__)

search_bp = Blueprint("search", __name__)


def _session():
    return get_session_context()


@search_bp.get("/search")
@login_required
async def search():
    raw_query = request.args.get("q", "")
    logger.debug("Route search raw query='%s'", raw_query)
    results: list = []
    truncation_notice: str | None = None
    sanitized_query = ""

    if raw_query:
        session = _session()
        user = await session.require_user()
        dek = await session.require_dek()

        try:
            sanitized_query, results, truncated = await get_search_api().search(
                user["id"], dek, raw_query
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
            limit = int(settings.LIMITS.max_search_query_length)
            truncation_notice = (
                f"Your search was truncated to the first {limit} characters."
            )

    logger.debug("Route returning %d results", len(results))
    return await render_template(
        "partials/search_results.html",
        results=results,
        has_query=bool(sanitized_query),
        truncation_notice=truncation_notice,
    )


FRECENT_TAG_LAMBDA = DEFAULT_FRECENCY_DECAY


@search_bp.get("/search/recent")
@login_required
async def recent_searches():
    session = _session()
    user = await session.require_user()

    dek = await session.require_dek()

    limit = int(settings.SEARCH.recent_suggestion_limit)
    lambda_param: Any = request.args.get("lambda")
    decay_constant = resolve_frecency_lambda(lambda_param, default=FRECENT_TAG_LAMBDA)
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
