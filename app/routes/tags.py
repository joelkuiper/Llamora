from quart import Blueprint, render_template, abort, request
from app import db
from app.services.auth_helpers import login_required, get_current_user, get_dek


tags_bp = Blueprint("tags", __name__)


@tags_bp.route("/t/<tag_hash>")
@login_required
async def tag_overview(tag_hash):
    if len(tag_hash) != 64:
        abort(404, description="Tag not found.")
    try:
        tag_bytes = bytes.fromhex(tag_hash)
    except ValueError:
        abort(404, description="Tag not found.")
    user = await get_current_user()
    dek = get_dek()
    try:
        overview = await db.get_tag_overview(user["id"], tag_bytes, dek)
    except Exception:
        abort(404, description="Tag not found.")
    template = (
        "partials/tag_overview.html" if request.headers.get("HX-Request") else "tag_overview.html"
    )
    return await render_template(template, overview=overview)

