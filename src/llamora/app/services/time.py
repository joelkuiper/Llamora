from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

from quart import request
import humanize as _humanize


logger = logging.getLogger(__name__)
_DEFAULT_LOCALE = "en-US"
_DEFAULT_HOUR_CYCLE = "24h"


def get_timezone() -> str:
    """Return client timezone as an IANA string.

    Prefers the ``tz`` query parameter, then the ``X-Timezone`` header, followed by
    a ``tz`` cookie. Defaults to ``UTC`` if unavailable.
    """
    tz = request.args.get("tz")
    if tz:
        try:
            ZoneInfo(tz)
            return tz
        except Exception:  # pragma: no cover - ZoneInfo raises various errors
            logger.debug("Invalid timezone '%s' from query parameter", tz)
    tz_header = request.headers.get("X-Timezone")
    if tz_header:
        try:
            ZoneInfo(tz_header)
            return tz_header
        except Exception:  # pragma: no cover - ZoneInfo raises various errors
            logger.debug("Invalid timezone '%s' from header", tz_header)
    tz_cookie = request.cookies.get("tz")
    if tz_cookie:
        try:
            ZoneInfo(tz_cookie)
            return tz_cookie
        except Exception:  # pragma: no cover - ZoneInfo raises various errors
            logger.debug("Invalid timezone '%s' from cookie", tz_cookie)
    return "UTC"


def get_locale() -> str:
    locale = request.args.get("locale")
    if locale:
        return locale
    header = request.headers.get("X-Locale")
    if header:
        return header
    cookie = request.cookies.get("locale")
    if cookie:
        return cookie
    return _DEFAULT_LOCALE


def get_hour_cycle() -> str:
    cycle = request.args.get("hc")
    if cycle:
        return cycle
    header = request.headers.get("X-Hour-Cycle")
    if header:
        return header
    cookie = request.cookies.get("hc")
    if cookie:
        return cookie
    return _DEFAULT_HOUR_CYCLE


def _use_24h(cycle: str | None) -> bool:
    if not cycle:
        return True
    normalized = str(cycle).strip().lower()
    if normalized in {"h23", "h24", "23", "24", "24h"}:
        return True
    if normalized in {"h11", "h12", "11", "12", "12h"}:
        return False
    return True


def local_date() -> date:
    """Get the current date in the client's timezone."""
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


def _coerce_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def format_time(value: datetime | str) -> str:
    dt = _coerce_datetime(value)
    tz = get_timezone()
    try:
        dt_local = dt.astimezone(ZoneInfo(tz))
    except Exception:
        dt_local = dt.astimezone(timezone.utc)
    if _use_24h(get_hour_cycle()):
        return dt_local.strftime("%H:%M")
    return dt_local.strftime("%I:%M %p").lstrip("0")


def format_timestamp(value: datetime | str) -> str:
    dt = _coerce_datetime(value)
    tz = get_timezone()
    try:
        dt_local = dt.astimezone(ZoneInfo(tz))
    except Exception:
        dt_local = dt.astimezone(timezone.utc)
    if _use_24h(get_hour_cycle()):
        return dt_local.strftime("%b %d, %Y %H:%M").replace(" 0", " ")
    return dt_local.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")


def date_and_part(user_time: str, tz: str) -> tuple[str, str]:
    """Compute date string and part of day from user time and timezone."""
    try:
        dt = datetime.fromisoformat(user_time.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    try:
        dt_local = dt.astimezone(ZoneInfo(tz))
    except Exception:
        dt_local = dt.astimezone(timezone.utc)
    return format_date(dt_local), part_of_day(dt_local)
