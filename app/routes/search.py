import logging

from quart import Blueprint, render_template, request, abort
from app.api.search import InvalidSearchQuery
from app.services.container import get_search_api
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)
from config import MAX_SEARCH_QUERY_LENGTH


logger = logging.getLogger(__name__)

search_bp = Blueprint("search", __name__)


@search_bp.get("/search")
@login_required
async def search():
    raw_query = request.args.get("q", "")
    logger.debug("Route search raw query='%s'", raw_query)
    results: list = []
    truncation_notice: str | None = None
    sanitized_query = ""

    if raw_query:
        user = await get_current_user()
        if user is None:
            abort(401)
            raise AssertionError("unreachable")
        dek = get_dek()
        if dek is None:
            abort(401, description="Missing encryption key")
            raise AssertionError("unreachable")

        try:
            sanitized_query, results, truncated = await get_search_api().search(
                user["id"], dek, raw_query
            )
        except InvalidSearchQuery:
            logger.info("Discarding invalid search query for user %s", user["id"])
            sanitized_query = ""
            results = []
            truncated = False

        if truncated:
            truncation_notice = (
                f"Your search was truncated to the first {MAX_SEARCH_QUERY_LENGTH} characters."
            )

    logger.debug("Route returning %d results", len(results))
    return await render_template(
        "partials/search_results.html",
        results=results,
        has_query=bool(sanitized_query),
        truncation_notice=truncation_notice,
    )
