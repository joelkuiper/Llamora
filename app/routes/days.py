from quart import Blueprint, redirect, request, url_for, render_template, make_response
from app.services.container import get_services
from app.services.auth_helpers import (
    login_required,
    get_current_user,
)
from app.services.chat_context import get_chat_context
from app.services.time import local_date
from app.services.calendar import get_month_context

days_bp = Blueprint("days", __name__)


def _db():
    return get_services().db


@days_bp.route("/")
@login_required
async def index():
    return redirect(url_for("days.day_today"), code=302)


async def _render_day(date: str, target: str | None):
    user = await get_current_user()
    uid = user["id"]
    context = await get_chat_context(user, date)
    html = await render_template(
        "index.html",
        user=user,
        day=date,
        content_template="partials/chat.html",
        scroll_target=target,
        **context,
    )
    resp = await make_response(html)
    await _db().users.update_state(uid, active_date=date)
    return resp


@days_bp.route("/d/today")
@login_required
async def day_today():
    today = local_date().isoformat()
    target = request.args.get("target")
    return await _render_day(today, target)


@days_bp.route("/d/<date>")
@login_required
async def day(date):
    target = request.args.get("target")
    return await _render_day(date, target)


@days_bp.route("/calendar")
@login_required
async def calendar_view():
    user = await get_current_user()
    today = local_date()
    requested_year = request.args.get("year", type=int)
    requested_month = request.args.get("month", type=int)
    if (
        requested_year is not None
        and requested_month is not None
        and requested_year >= 1
        and 1 <= requested_month <= 12
    ):
        target_year = requested_year
        target_month = requested_month
    else:
        target_year = today.year
        target_month = today.month
    context = await get_month_context(
        user["id"], target_year, target_month, today=today
    )
    html = await render_template(
        "partials/calendar_popover.html",
        **context,
    )
    return await make_response(html)


@days_bp.route("/calendar/<int:year>/<int:month>")
@login_required
async def calendar_month(year: int, month: int):
    user = await get_current_user()
    context = await get_month_context(user["id"], year, month)
    html = await render_template(
        "partials/calendar.html",
        **context,
    )
    return await make_response(html)
