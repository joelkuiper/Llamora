from datetime import datetime, timedelta
from quart import request


def get_timezone_offset() -> int:
    """Return client timezone offset in minutes from UTC.

    Checks the ``tz`` query parameter first, then a ``tz`` cookie. If neither
    is provided or cannot be parsed, defaults to ``0`` (UTC).
    """
    tz = request.args.get("tz", type=int)
    if tz is not None:
        return tz
    tz_cookie = request.cookies.get("tz")
    try:
        return int(tz_cookie)
    except (TypeError, ValueError):
        return 0


def local_date() -> datetime.date:
    """Get the current date in the client's timezone."""
    offset = get_timezone_offset()
    return (datetime.utcnow() - timedelta(minutes=offset)).date()
