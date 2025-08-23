from datetime import datetime
from quart import Blueprint, redirect, url_for, render_template, make_response
from app import db
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)

sessions_bp = Blueprint("sessions", __name__)


@sessions_bp.route("/")
@login_required
async def index():
    user = await get_current_user()
    uid = user["id"]
    state = await db.get_state(uid)
    current_date = state.get("active_date")
    if not current_date:
        current_date = datetime.utcnow().date().isoformat()
    await db.update_state(uid, active_date=current_date)
    return redirect(url_for("sessions.session", session_id=current_date), code=302)


@sessions_bp.route("/s/<session_id>")
@login_required
async def session(session_id):
    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()
    history = await db.get_history(uid, session_id, dek)
    pending_msg_id = None
    if history and history[-1]["role"] == "user":
        pending_msg_id = history[-1]["id"]
    html = await render_template(
        "index.html",
        user=user,
        history=history,
        session={"id": session_id, "name": session_id},
        sessions=[],
        pending_msg_id=pending_msg_id,
        content_template="partials/chat.html",
    )
    resp = await make_response(html)
    await db.update_state(uid, active_date=session_id)
    return resp
