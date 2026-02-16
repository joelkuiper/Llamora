"""Calendar-related service helpers."""

from __future__ import annotations

import calendar as _calendar
from datetime import date

from llamora.app.services.container import get_services
from llamora.app.services.crypto import CryptoContext
from llamora.app.services.time import local_date


def _offset_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = (year * 12 + month - 1) + delta
    new_year = total // 12
    new_month = total % 12 + 1
    return new_year, new_month


def _nav_months(
    year: int,
    month: int,
    min_month: date,
    max_month: date,
) -> tuple[int, int, int, int]:
    """Return previous/next months clamped to the allowed range."""

    prev_year, prev_month = _offset_month(year, month, -1)
    next_year, next_month = _offset_month(year, month, 1)
    prev_date = date(prev_year, prev_month, 1)
    next_date = date(next_year, next_month, 1)

    if prev_date < min_month:
        prev_year, prev_month = min_month.year, min_month.month
    if next_date > max_month:
        next_year, next_month = max_month.year, max_month.month

    return prev_year, prev_month, next_year, next_month


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


async def get_month_context(
    ctx: CryptoContext,
    year: int,
    month: int,
    *,
    today: date | None = None,
) -> dict:
    """Return template context for a given calendar month.

    Args:
        user_id: The user whose calendar data should be retrieved.
        year: Calendar year to render.
        month: Calendar month to render (1-12).
        today: Optional date to treat as "today" for context. When omitted the
            value is derived from :func:`app.services.time.local_date`.
    """

    today_date = today or local_date()
    today_iso = today_date.isoformat()

    services = get_services()
    state = await services.db.users.get_state(ctx.user_id)
    min_date_iso = await services.db.entries.get_first_entry_date(ctx.user_id)
    if min_date_iso:
        min_date_obj = date.fromisoformat(min_date_iso)
    else:
        min_date_obj = today_date

    min_month_start = date(min_date_obj.year, min_date_obj.month, 1)
    max_month_start = date(today_date.year, today_date.month, 1)

    try:
        requested_month = date(year, month, 1)
    except ValueError:
        requested_month = max_month_start

    if requested_month < min_month_start:
        requested_month = min_month_start
    if requested_month > max_month_start:
        requested_month = max_month_start

    selected_year = requested_month.year
    selected_month = requested_month.month

    stored_active_iso = state.get("active_date")
    active_candidate = today_date
    if stored_active_iso:
        try:
            active_candidate = date.fromisoformat(stored_active_iso)
        except ValueError:
            active_candidate = today_date

    if active_candidate < min_date_obj:
        active_candidate = min_date_obj
    if active_candidate > today_date:
        active_candidate = today_date

    active_day_iso = active_candidate.isoformat()
    if (
        active_candidate.year == selected_year
        and active_candidate.month == selected_month
    ):
        month_days = _calendar.monthrange(selected_year, selected_month)[1]
        month_min_day = (
            min_date_obj.day
            if selected_year == min_date_obj.year
            and selected_month == min_date_obj.month
            else 1
        )
        month_max_day = month_days
        if selected_year == today_date.year and selected_month == today_date.month:
            month_max_day = min(month_max_day, today_date.day)
        desired_day = active_candidate.day
        clamped_day = _clamp(desired_day, month_min_day, month_max_day)
        active_day_iso = date(selected_year, selected_month, clamped_day).isoformat()
    active_days, opening_only_days = await services.db.entries.get_days_with_entries(
        ctx.user_id, selected_year, selected_month
    )
    day_summary_digests = await services.db.entries.get_day_summary_digests(
        ctx.user_id, selected_year, selected_month
    )
    prev_year, prev_month, next_year, next_month = _nav_months(
        selected_year, selected_month, min_month_start, max_month_start
    )

    month_names = [name for name in _calendar.month_name[1:]]
    return {
        "year": selected_year,
        "month": selected_month,
        "month_name": _calendar.month_name[selected_month],
        "weeks": _calendar.Calendar().monthdayscalendar(selected_year, selected_month),
        "active_day": active_day_iso,
        "today": today_iso,
        "today_year": today_date.year,
        "today_month": today_date.month,
        "today_day": today_date.day,
        "min_date": min_date_obj.isoformat(),
        "min_year": min_date_obj.year,
        "min_month": min_date_obj.month,
        "min_day": min_date_obj.day,
        "month_names": month_names,
        "active_days": active_days,
        "opening_only_days": opening_only_days,
        "day_summary_digests": day_summary_digests,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }


__all__ = ["get_month_context"]
