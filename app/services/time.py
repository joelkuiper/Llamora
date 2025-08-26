from datetime import datetime
from zoneinfo import ZoneInfo
from quart import request


def get_timezone() -> str:
    """Return client timezone as an IANA string.

    Prefers the ``tz`` query parameter, then a ``tz`` cookie. Defaults to ``UTC``
    if unavailable.
    """
    tz = request.args.get("tz")
    if tz:
        return tz
    tz_cookie = request.cookies.get("tz")
    if tz_cookie:
        return tz_cookie
    return "UTC"


def local_date() -> datetime.date:
    """Get the current date in the client's timezone."""
    tz = get_timezone()
    try:
        return datetime.now(ZoneInfo(tz)).date()
    except Exception:
        return datetime.utcnow().date()


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


def date_and_part(user_time: str, tz: str) -> tuple[str, str]:
    """Compute date string and part of day from user time and timezone."""
    try:
        dt = datetime.fromisoformat(user_time.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    try:
        dt_local = dt.astimezone(ZoneInfo(tz))
    except Exception:
        dt_local = dt.astimezone(ZoneInfo("UTC"))
    return format_date(dt_local), part_of_day(dt_local)
