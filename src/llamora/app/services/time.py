from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
from urllib.parse import unquote

from quart import request
import humanize as _humanize


logger = logging.getLogger(__name__)


def _normalize_timezone(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    if "%" in value:
        try:
            value = unquote(value)
        except Exception:
            pass
    return value


def get_timezone() -> str:
    """Return client timezone as an IANA string.

    Prefers the ``tz`` query parameter, then the ``X-Timezone`` header, followed by
    a ``tz`` cookie. Defaults to ``UTC`` if unavailable.
    """
    tz = _normalize_timezone(request.args.get("tz"))
    if tz:
        try:
            ZoneInfo(tz)
            return tz
        except Exception:  # pragma: no cover - ZoneInfo raises various errors
            logger.debug("Invalid timezone '%s' from query parameter", tz)
    tz_header = _normalize_timezone(request.headers.get("X-Timezone"))
    if tz_header:
        try:
            ZoneInfo(tz_header)
            return tz_header
        except Exception:  # pragma: no cover - ZoneInfo raises various errors
            logger.debug("Invalid timezone '%s' from header", tz_header)
    tz_cookie = _normalize_timezone(request.cookies.get("tz"))
    if tz_cookie:
        try:
            ZoneInfo(tz_cookie)
            return tz_cookie
        except Exception:  # pragma: no cover - ZoneInfo raises various errors
            logger.debug("Invalid timezone '%s' from cookie", tz_cookie)
    return "UTC"


def local_date() -> date:
    """Get the current date in the client's timezone."""
    client_today = request.args.get("client_today")
    if client_today:
        try:
            return date.fromisoformat(client_today)
        except ValueError:
            logger.debug("Invalid client today query '%s'", client_today)
    client_today = request.headers.get("X-Client-Today")
    if client_today:
        try:
            return date.fromisoformat(client_today)
        except ValueError:
            logger.debug("Invalid client today header '%s'", client_today)
    tz = get_timezone()
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def part_of_day(dt: datetime) -> str:
    h = dt.hour
    if 0 <= h < 5:
        return "night"
    if 5 <= h < 8:
        return "early-morning"
    if 8 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    return "late-night"


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_date(dt: datetime) -> str:
    return f"{ordinal(dt.day)} of {dt.strftime('%B %Y')}"


def humanize(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _humanize.naturaltime(value)


def date_and_part(user_time: str, tz: str) -> tuple[str, str]:
    """Compute date string and part of day from user time and timezone."""
    try:
        dt = datetime.fromisoformat(user_time.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    try:
        tz_value = _normalize_timezone(tz) or "UTC"
        dt_local = dt.astimezone(ZoneInfo(tz_value))
    except Exception:
        dt_local = dt.astimezone(timezone.utc)
    return format_date(dt_local), part_of_day(dt_local)
