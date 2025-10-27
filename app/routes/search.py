import logging

from quart import Blueprint, render_template, request
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
    query = raw_query.strip()
    logger.debug("Route search raw query='%s'", raw_query)
    results = []
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        logger.info(
            "Search query length %d exceeds limit of %d; returning no results",
            len(query),
            MAX_SEARCH_QUERY_LENGTH,
        )
        query = query[:MAX_SEARCH_QUERY_LENGTH]
    elif query:
        user = await get_current_user()
        dek = get_dek()
        results = await get_search_api().search(user["id"], dek, query)
    logger.debug("Route returning %d results", len(results))
    return await render_template(
        "partials/search_results.html",
        results=results,
        has_query=bool(query),
    )
