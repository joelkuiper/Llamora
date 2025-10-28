from quart import (
    Blueprint,
    redirect,
    request,
    url_for,
    render_template,
    make_response,
    abort,
)
from markupsafe import Markup
from app.services.container import get_services
from app.services.auth_helpers import (
    login_required,
    get_current_user,
)
from app.services.time import local_date
from app.services.validators import parse_iso_date
from app.services.calendar import get_month_context
from app.routes.chat import render_chat

days_bp = Blueprint("days", __name__)


def _db():
    return get_services().db


@days_bp.route("/")
@login_required
async def index():
    return redirect(url_for("days.day_today"), code=302)


async def _render_day(date: str, target: str | None):
    render_result = await render_chat(date, oob=False, scroll_target=target)
    chat_markup = Markup(render_result.html)
    html = await render_template(
        "index.html",
        day=render_result.active_date,
        chat_html=chat_markup,
        scroll_target=target,
    )
    resp = await make_response(html, 200)
    await _db().users.update_state(
        render_result.user_id, active_date=render_result.active_date
    )
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
    try:
        normalized_date = parse_iso_date(date)
    except ValueError:
        abort(400, description="Invalid date")
    target = request.args.get("target")
    return await _render_day(normalized_date, target)


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
