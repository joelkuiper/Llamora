import logging
from quart import Blueprint, render_template, request
from app import search_api
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)


logger = logging.getLogger(__name__)

search_bp = Blueprint("search", __name__)


@search_bp.get("/search")
@login_required
async def search():
    query = request.args.get("q", "").strip()
    logger.debug("Route search query='%s'", query)
    results = []
    if query:
        user = await get_current_user()
        dek = get_dek()
        results = await search_api.search(user["id"], dek, query)
    logger.debug("Route returning %d results", len(results))
    return await render_template("partials/search_results.html", results=results)
