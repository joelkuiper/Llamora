import asyncio
from collections.abc import Callable, Coroutine
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
    abort_http,
    build_view_state,
    build_tags_catalog_payload,
    get_summary_timeout_seconds,
    is_htmx_request,
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

# Process-local deduplication only; not shared across multiple workers/processes.
_day_summary_singleflight: dict[tuple[str, str, str], asyncio.Task[str]] = {}
_day_summary_singleflight_lock = asyncio.Lock()


async def _run_day_summary_singleflight(
    key: tuple[str, str, str],
    producer: Callable[[], Coroutine[object, object, str]],
) -> str:
    async with _day_summary_singleflight_lock:
        task = _day_summary_singleflight.get(key)
        if task is not None and task.done():
            _day_summary_singleflight.pop(key, None)
            task = None
        if task is None:
            task = asyncio.create_task(producer())
            _day_summary_singleflight[key] = task

    try:
        return await task
    finally:
        async with _day_summary_singleflight_lock:
            _day_summary_singleflight.pop(key, None)


async def _json_response(payload: dict[str, object], status: int = 200):
    resp = await make_response(orjson.dumps(payload), status)
    resp.mimetype = "application/json"
    return resp


def _resolve_calendar_request(
    *,
    default_year: int,
    default_month: int,
) -> tuple[int, int, str]:
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
        target_year = default_year
        target_month = default_month
    if mode not in {"calendar", "picker"}:
        mode = "calendar"
    return target_year, target_month, mode


@days_bp.route("/")
@login_required
async def index():
    return redirect(url_for("days.day_today"), code=302)


async def _render_day(date: str, target: str | None, view_kind: str):
    session = get_session_context()
    user = await session.require_user()
    _, _, ctx = await require_encryption_context(session)
    services = get_services()
    today = local_date().isoformat()
    min_date = await services.db.entries.get_first_entry_date(user["id"]) or today
    is_first_day = date == min_date
    view = "diary"
    logger.debug(
        "Render day=%s min_date=%s is_first_day=%s", date, min_date, is_first_day
    )
    target_param = (request.args.get("target") or "").strip() or None
    entries_response = await render_entries(
        date,
        oob=False,
        scroll_target=target,
        view_kind=view_kind,
    )
    entries_html = await entries_response.get_data(as_text=True)
    tags_catalog_items = await build_tags_catalog_payload(ctx)
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
        "tags_catalog_items": tags_catalog_items,
        "target": target_param,
        "view_state": build_view_state(
            view=view,
            day=date,
            target=target_param,
        ),
    }
    if is_htmx_request() and request.headers.get("HX-Target") == "main-content":
        html = await render_template("components/shared/main_content.html", **context)
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
    target_year, target_month, mode = _resolve_calendar_request(
        default_year=today.year,
        default_month=today.month,
    )
    html = await _render_calendar(target_year, target_month, today=today, mode=mode)
    return await make_response(html)


@days_bp.route("/calendar/<int:year>/<int:month>")
@login_required
async def calendar_month(year: int, month: int):
    target_year, target_month, mode = _resolve_calendar_request(
        default_year=year,
        default_month=month,
    )
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
    summary_timeout_seconds = get_summary_timeout_seconds()
    digest = await summarize.get_day_digest(ctx, normalized_date)

    cached_summary = await summarize.get_cached(
        ctx, "summary", f"day:{normalized_date}", digest
    )
    if cached_summary is not None:
        return await _json_response({"summary": cached_summary})

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
            await asyncio.wait_for(
                generate_day_summary(
                    services.llm_service.llm,
                    normalized_date,
                    entries,
                ),
                timeout=summary_timeout_seconds,
            )
        ).strip()
        await summarize.cache(ctx, "summary", f"day:{normalized_date}", digest, text)
        return text

    try:
        summary = await _run_day_summary_singleflight(
            (user_id, normalized_date, digest),
            _generate_and_cache_summary,
        )
    except asyncio.TimeoutError:
        logger.warning("Day summary generation timed out for date=%s", normalized_date)
        abort_http(504, "Summary generation timed out.")
    return await _json_response({"summary": summary or ""})
