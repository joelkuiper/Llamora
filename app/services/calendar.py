"""Calendar-related service helpers."""

from __future__ import annotations

import calendar as _calendar
from datetime import date

from app.services.container import get_services
from app.services.time import local_date


def _nav_months(year: int, month: int) -> tuple[int, int, int, int]:
    """Return previous and next month navigation metadata."""

    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year
    return prev_year, prev_month, next_year, next_month


async def get_month_context(
    user_id: str,
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
    state = await services.db.users.get_state(user_id)
    active_days = await services.db.messages.get_days_with_messages(
        user_id, year, month
    )
    prev_year, prev_month, next_year, next_month = _nav_months(year, month)

    return {
        "year": year,
        "month": month,
        "month_name": _calendar.month_name[month],
        "weeks": _calendar.Calendar().monthdayscalendar(year, month),
        "active_day": state.get("active_date", today_iso),
        "today": today_iso,
        "active_days": active_days,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }


__all__ = ["get_month_context"]
