from datetime import datetime
import calendar
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
    return redirect(url_for("sessions.session", date=current_date), code=302)


@sessions_bp.route("/d/<date>")
@login_required
async def session(date):
    user = await get_current_user()
    uid = user["id"]
    dek = get_dek()
    history = await db.get_history(uid, date, dek)
    pending_msg_id = None
    if history and history[-1]["role"] == "user":
        pending_msg_id = history[-1]["id"]
    html = await render_template(
        "index.html",
        user=user,
        history=history,
        day=date,
        pending_msg_id=pending_msg_id,
        content_template="partials/chat.html",
    )
    resp = await make_response(html)
    await db.update_state(uid, active_date=date)
    return resp


@sessions_bp.route("/calendar")
@login_required
async def calendar_view():
    user = await get_current_user()
    today = datetime.utcnow().date()
    weeks = calendar.Calendar().monthdayscalendar(today.year, today.month)
    state = await db.get_state(user["id"])
    active_day = state.get("active_date", today.isoformat())
    html = await render_template(
        "partials/calendar.html",
        year=today.year,
        month=today.month,
        month_name=calendar.month_name[today.month],
        weeks=weeks,
        active_day=active_day,
    )
    resp = await make_response(html)
    resp.headers["HX-Push-Url"] = url_for("sessions.calendar_view")
    return resp
