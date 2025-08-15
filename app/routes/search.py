from quart import Blueprint, render_template, request
from app import search_api
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)

search_bp = Blueprint("search", __name__)


@search_bp.get("/search")
@login_required
async def search():
    query = request.args.get("q", "").strip()
    session_id = request.args.get("session_id", "").strip()
    results = []
    if query:
        user = await get_current_user()
        dek = get_dek()
        results = await search_api.search(user["id"], dek, query)
        if session_id:
            results = [r for r in results if r["session_id"] == session_id]
    return await render_template("partials/search_results.html", results=results)
