from __future__ import annotations

from typing import Any, Mapping

from quart import abort, g

from llamora.app.services.crypto import CryptoContext
from llamora.app.services.validators import parse_iso_date
from llamora.app.services.session_context import SessionContext, get_session_context


def require_iso_date(raw: str) -> str:
    """Parse an ISO date string or abort with a 400 error."""

    try:
        return parse_iso_date(raw)
    except ValueError as exc:
        abort(400, description="Invalid date")
        raise AssertionError("unreachable") from exc


async def require_encryption_context(
    session: SessionContext | None = None,
) -> tuple[SessionContext, Mapping[str, Any], CryptoContext]:
    """Require an authenticated user and return an encryption context."""

    session = session or get_session_context()
    user = await session.require_user()
    existing = getattr(g, "_crypto_context", None)
    if existing is not None:
        return session, user, existing
    dek = await session.require_dek()
    epoch_raw = user.get("current_epoch")
    try:
        epoch = int(epoch_raw) if epoch_raw is not None else 1
    except (TypeError, ValueError):
        epoch = 1
    ctx = CryptoContext(user_id=str(user["id"]), dek=dek, epoch=epoch)
    g._crypto_context = ctx
    return session, user, ctx


async def ensure_entry_exists(db: Any, user_id: str, entry_id: str) -> None:
    """Ensure the given entry exists for the user or abort with 404."""

    if not await db.entries.entry_exists(user_id, entry_id):
        abort(404, description="entry not found")
        raise AssertionError("unreachable")
