from quart import (
    Blueprint,
    redirect,
    request,
    url_for,
    render_template,
    make_response,
)
from llamora.app.services.auth_helpers import login_required
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import local_date
from llamora.app.routes.helpers import require_iso_date
from llamora.app.services.calendar import get_month_context
from llamora.app.routes.entries import render_entries
from llamora.app.services.container import get_services
from llamora.app.services.tag_presenter import present_tags_view_data
from llamora.app.services.day_summary import generate_day_summary
from llamora.app.routes.helpers import require_user_and_dek
from logging import getLogger
import orjson

days_bp = Blueprint("days", __name__)
logger = getLogger(__name__)


@days_bp.route("/")
@login_required
async def index():
    return redirect(url_for("days.day_today"), code=302)


async def _render_day(date: str, target: str | None, view_kind: str):
    session = get_session_context()
    user = await session.require_user()
    services = get_services()
    today = local_date().isoformat()
    min_date = await services.db.entries.get_first_entry_date(user["id"]) or today
    is_first_day = date == min_date
    view = request.args.get("view", "diary")
    if view not in {"diary", "tags", "structure"}:
        view = "diary"
    logger.debug(
        "Render day=%s min_date=%s is_first_day=%s", date, min_date, is_first_day
    )
    entries_html = None
    tags_view = None
    selected_tag = None
    tags_sort_kind = services.tag_service.normalize_tags_sort_kind(
        request.args.get("sort_kind")
    )
    tags_sort_dir = services.tag_service.normalize_tags_sort_dir(
        request.args.get("sort_dir")
    )
    legacy_sort = services.tag_service.normalize_legacy_sort(request.args.get("sort"))
    if legacy_sort is not None:
        tags_sort_kind, tags_sort_dir = legacy_sort
    try:
        entries_limit = int(request.args.get("entries_limit") or 12)
    except (TypeError, ValueError):
        entries_limit = 12
    entries_limit = max(6, min(entries_limit, 60))
    target_param = (request.args.get("target") or "").strip() or None
    if view == "diary":
        entries_response = await render_entries(
            date,
            oob=False,
            scroll_target=target,
            view_kind=view_kind,
        )
        entries_html = await entries_response.get_data(as_text=True)
    elif view == "tags":
        dek = await session.require_dek()
        selected_tag = services.tag_service.normalize_tag_query(request.args.get("tag"))
        tags_view = await services.tag_service.get_tags_view_data(
            user["id"],
            dek,
            selected_tag,
            sort_kind=tags_sort_kind,
            sort_dir=tags_sort_dir,
            entry_limit=entries_limit,
        )
        tags_view = present_tags_view_data(tags_view)
        selected_tag = tags_view.selected_tag
    context = {
        "day": date,
        "is_today": date == today,
        "today": today,
        "min_date": min_date,
        "is_first_day": is_first_day,
        "entries_html": entries_html,
        "scroll_target": target,
        "view_kind": view_kind,
        "view": view,
        "tags_view": tags_view,
        "selected_tag": selected_tag,
        "tags_sort_kind": tags_sort_kind,
        "tags_sort_dir": tags_sort_dir,
        "entries_limit": entries_limit,
        "target": target_param,
    }
    if request.headers.get("HX-Request"):
        target_id = request.headers.get("HX-Target")
        if target_id == "tags-view-list" and view == "tags":
            html = await render_template(
                "partials/tags_view_list_fragment.html", **context
            )
            return await make_response(html, 200)
        if target_id == "main-content":
            html = await render_template("partials/main_content.html", **context)
            return await make_response(html, 200)
    html = await render_template("index.html", **context)
    return await make_response(html, 200)


async def _render_calendar(year: int, month: int, *, today=None, mode="calendar"):
    _, user, dek = await require_user_and_dek()
    context = await get_month_context(user["id"], year, month, dek, today=today)
    context["mode"] = mode
    template = (
        "partials/calendar_popover.html"
        if request.endpoint == "days.calendar_view"
        else "partials/calendar.html"
    )
    return await render_template(
        template,
        **context,
    )


@days_bp.route("/d/today")
@login_required
async def day_today():
    today = local_date().isoformat()
    target = request.args.get("target")
    return await _render_day(today, target, "today")


@days_bp.route("/d/<date>")
@login_required
async def day(date):
    normalized_date = require_iso_date(date)
    target = request.args.get("target")
    return await _render_day(normalized_date, target, "day")


@days_bp.route("/calendar")
@login_required
async def calendar_view():
    today = local_date()
    requested_year = request.args.get("year", type=int)
    requested_month = request.args.get("month", type=int)
    mode = request.args.get("mode", "calendar")
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
    if mode not in {"calendar", "picker"}:
        mode = "calendar"
    html = await _render_calendar(target_year, target_month, today=today, mode=mode)
    return await make_response(html)


@days_bp.route("/calendar/<int:year>/<int:month>")
@login_required
async def calendar_month(year: int, month: int):
    requested_year = request.args.get("year", type=int)
    requested_month = request.args.get("month", type=int)
    mode = request.args.get("mode", "calendar")
    if (
        requested_year is not None
        and requested_month is not None
        and requested_year >= 1
        and 1 <= requested_month <= 12
    ):
        target_year = requested_year
        target_month = requested_month
    else:
        target_year = year
        target_month = month
    if mode not in {"calendar", "picker"}:
        mode = "calendar"
    html = await _render_calendar(target_year, target_month, mode=mode)
    return await make_response(html)


@days_bp.route("/d/<date>/summary")
@login_required
async def day_summary(date):
    normalized_date = require_iso_date(date)
    _, user, dek = await require_user_and_dek()
    services = get_services()
    entries = await services.db.entries.get_flat_entries_for_date(
        user["id"], normalized_date, dek
    )
    summary = await generate_day_summary(
        services.llm_service.llm,
        normalized_date,
        entries,
    )
    payload = {"summary": summary or ""}
    resp = await make_response(orjson.dumps(payload), 200)
    resp.mimetype = "application/json"
    return resp
