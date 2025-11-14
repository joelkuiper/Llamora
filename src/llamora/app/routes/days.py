from quart import (
    Blueprint,
    redirect,
    request,
    url_for,
    render_template,
    make_response,
    abort,
)
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import local_date
from llamora.app.services.validators import parse_iso_date
from llamora.app.services.calendar import get_month_context
from llamora.app.routes.chat import render_chat

days_bp = Blueprint("days", __name__)


def _session():
    return get_session_context()


@days_bp.route("/")
@login_required
async def index():
    return redirect(url_for("days.day_today"), code=302)


async def _render_day(date: str, target: str | None, view_kind: str):
    today = local_date().isoformat()
    chat_response = await render_chat(
        date,
        oob=False,
        scroll_target=target,
        view_kind=view_kind,
    )
    chat_html = await chat_response.get_data(as_text=True)
    html = await render_template(
        "index.html",
        day=date,
        is_today=date == today,
        today=today,
        chat_html=chat_html,
        scroll_target=target,
        view_kind=view_kind,
    )
    resp = await make_response(html, 200)
    return resp


@days_bp.route("/d/today")
@login_required
async def day_today():
    today = local_date().isoformat()
    target = request.args.get("target")
    return await _render_day(today, target, "today")


@days_bp.route("/d/<date>")
@login_required
async def day(date):
    try:
        normalized_date = parse_iso_date(date)
    except ValueError as exc:
        abort(400, description="Invalid date")
        raise AssertionError("unreachable") from exc
    target = request.args.get("target")
    return await _render_day(normalized_date, target, "day")


@days_bp.route("/calendar")
@login_required
async def calendar_view():
    session = _session()
    user = await session.require_user()
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
    session = _session()
    user = await session.require_user()
    context = await get_month_context(user["id"], year, month)
    html = await render_template(
        "partials/calendar.html",
        **context,
    )
    return await make_response(html)
