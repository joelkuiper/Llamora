from __future__ import annotations

from typing import Any, Mapping

from quart import abort

from llamora.app.services.validators import parse_iso_date
from llamora.app.services.session_context import SessionContext, get_session_context


def require_iso_date(raw: str) -> str:
    """Parse an ISO date string or abort with a 400 error."""

    try:
        return parse_iso_date(raw)
    except ValueError as exc:
        abort(400, description="Invalid date")
        raise AssertionError("unreachable") from exc


async def require_user_and_dek(
    session: SessionContext | None = None,
) -> tuple[SessionContext, Mapping[str, Any], bytes]:
    """Require an authenticated user and associated DEK for the request."""

    session = session or get_session_context()
    user = await session.require_user()
    dek = await session.require_dek()
    return session, user, dek


async def ensure_message_exists(db: Any, user_id: str, msg_id: str) -> None:
    """Ensure the given message exists for the user or abort with 404."""

    if not await db.messages.message_exists(user_id, msg_id):
        abort(404, description="message not found")
        raise AssertionError("unreachable")
