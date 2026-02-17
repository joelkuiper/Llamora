import asyncio
from collections.abc import Awaitable, Callable
from logging import getLogger

import orjson
from quart import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from llamora.app.routes.entries import render_entries
from llamora.app.services.auth_helpers import login_required
from llamora.app.routes.helpers import (
    build_view_state,
    require_encryption_context,
    require_iso_date,
)
from llamora.app.services.calendar import get_month_context
from llamora.app.services.container import get_services, get_summarize_service
from llamora.app.services.day_summary import generate_day_summary
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import local_date

days_bp = Blueprint("days", __name__)
logger = getLogger(__name__)

_day_summary_singleflight: dict[tuple[str, str, str], asyncio.Task[str]] = {}
_day_summary_singleflight_lock = asyncio.Lock()


async def _run_day_summary_singleflight(
    key: tuple[str, str, str],
    producer: Callable[[], Awaitable[str]],
) -> str:
    async with _day_summary_singleflight_lock:
        task = _day_summary_singleflight.get(key)
        if task is None:
            task = asyncio.create_task(producer())
            _day_summary_singleflight[key] = task

    try:
        return await task
    finally:
        async with _day_summary_singleflight_lock:
            if _day_summary_singleflight.get(key) is task:
                _day_summary_singleflight.pop(key, None)


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
    view = "diary"
    logger.debug(
        "Render day=%s min_date=%s is_first_day=%s", date, min_date, is_first_day
    )
    entries_html = None
    target_param = (request.args.get("target") or "").strip() or None
    entries_response = await render_entries(
        date,
        oob=False,
        scroll_target=target,
        view_kind=view_kind,
    )
    entries_html = await entries_response.get_data(as_text=True)
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
        "target": target_param,
        "view_state": build_view_state(
            view=view,
            day=date,
            target=target_param,
        ),
    }
    if request.headers.get("HX-Request"):
        target_id = request.headers.get("HX-Target")
        if target_id == "main-content":
            html = await render_template(
                "components/shared/main_content.html", **context
            )
            return await make_response(html, 200)
    html = await render_template("pages/index.html", **context)
    return await make_response(html, 200)


async def _render_calendar(year: int, month: int, *, today=None, mode="calendar"):
    _, user, ctx = await require_encryption_context()
    context = await get_month_context(ctx, year, month, today=today)
    context["mode"] = mode
    template = (
        "components/calendar/calendar_popover.html"
        if request.endpoint == "days.calendar_view"
        else "components/calendar/calendar.html"
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
    _, user, ctx = await require_encryption_context()
    services = get_services()
    user_id = user["id"]
    summarize = get_summarize_service()
    digest = await summarize.get_day_digest(ctx, normalized_date)

    cached_summary = await summarize.get_cached(
        ctx, "summary", f"day:{normalized_date}", digest
    )
    if cached_summary is not None:
        payload = {"summary": cached_summary}
        resp = await make_response(orjson.dumps(payload), 200)
        resp.mimetype = "application/json"
        return resp

    async def _generate_and_cache_summary() -> str:
        cache_hit = await summarize.get_cached(
            ctx, "summary", f"day:{normalized_date}", digest
        )
        if cache_hit is not None:
            return cache_hit
        entries = await services.db.entries.get_flat_entries_for_date(
            ctx, normalized_date
        )
        text = (
            await generate_day_summary(
                services.llm_service.llm,
                normalized_date,
                entries,
            )
        ).strip()
        await summarize.cache(ctx, "summary", f"day:{normalized_date}", digest, text)
        return text

    summary = await _run_day_summary_singleflight(
        (user_id, normalized_date, digest),
        _generate_and_cache_summary,
    )
    payload = {"summary": summary or ""}
    resp = await make_response(orjson.dumps(payload), 200)
    resp.mimetype = "application/json"
    return resp
