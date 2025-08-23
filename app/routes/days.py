from datetime import datetime
import calendar
from quart import Blueprint, redirect, url_for, render_template, make_response
from app import db
from app.services.auth_helpers import (
    login_required,
    get_current_user,
    get_dek,
)

days_bp = Blueprint("days", __name__)


def _nav_months(year: int, month: int) -> tuple[int, int, int, int]:
    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year
    return prev_year, prev_month, next_year, next_month


@days_bp.route("/")
@login_required
async def index():
    user = await get_current_user()
    uid = user["id"]
    state = await db.get_state(uid)
    today = datetime.utcnow().date().isoformat()
    current_date = state.get("active_date", today)
    if current_date != today:
        current_date = today
    await db.update_state(uid, active_date=current_date)
    return redirect(url_for("days.day", date=current_date), code=302)


@days_bp.route("/d/<date>")
@login_required
async def day(date):
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


@days_bp.route("/calendar")
@login_required
async def calendar_view():
    user = await get_current_user()
    today = datetime.utcnow().date()
    state = await db.get_state(user["id"])
    weeks = calendar.Calendar().monthdayscalendar(today.year, today.month)
    active_days = await db.get_days_with_messages(user["id"], today.year, today.month)
    prev_year, prev_month, next_year, next_month = _nav_months(today.year, today.month)
    html = await render_template(
        "partials/calendar_popover.html",
        year=today.year,
        month=today.month,
        month_name=calendar.month_name[today.month],
        weeks=weeks,
        active_day=state.get("active_date", today.isoformat()),
        today=today.isoformat(),
        active_days=active_days,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )
    return await make_response(html)


@days_bp.route("/calendar/<int:year>/<int:month>")
@login_required
async def calendar_month(year: int, month: int):
    user = await get_current_user()
    state = await db.get_state(user["id"])
    weeks = calendar.Calendar().monthdayscalendar(year, month)
    active_days = await db.get_days_with_messages(user["id"], year, month)
    prev_year, prev_month, next_year, next_month = _nav_months(year, month)
    html = await render_template(
        "partials/calendar.html",
        year=year,
        month=month,
        month_name=calendar.month_name[month],
        weeks=weeks,
        active_day=state.get("active_date", datetime.utcnow().date().isoformat()),
        today=datetime.utcnow().date().isoformat(),
        active_days=active_days,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )
    return await make_response(html)
