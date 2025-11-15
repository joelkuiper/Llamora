from __future__ import annotations

from quart import abort

from llamora.app.services.validators import parse_iso_date


def require_iso_date(raw: str) -> str:
    """Parse an ISO date string or abort with a 400 error."""

    try:
        return parse_iso_date(raw)
    except ValueError as exc:
        abort(400, description="Invalid date")
        raise AssertionError("unreachable") from exc
