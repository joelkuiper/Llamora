from __future__ import annotations

from typing import Any, Mapping

from quart import abort, g

from llamora.app.services.container import get_tag_service
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
        epoch = int(epoch_raw) if epoch_raw is not None else 0
    except (TypeError, ValueError):
        epoch = 0
    if epoch <= 0:
        abort(500, description="Missing encryption epoch metadata")
    ctx = CryptoContext(user_id=str(user["id"]), dek=dek, epoch=epoch)
    g._crypto_context = ctx
    return session, user, ctx


async def ensure_entry_exists(db: Any, user_id: str, entry_id: str) -> None:
    """Ensure the given entry exists for the user or abort with 404."""

    if not await db.entries.entry_exists(user_id, entry_id):
        abort(404, description="entry not found")
        raise AssertionError("unreachable")


def build_view_state(
    *,
    view: str,
    day: str | None = None,
    selected_tag: str | None = None,
    sort_kind: str | None = None,
    sort_dir: str | None = None,
    target: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a canonical view-state snapshot for the frontend."""

    state: dict[str, Any] = {"view": view}
    if day:
        state["day"] = day
    if selected_tag:
        state["selected_tag"] = selected_tag
    if sort_kind:
        state["sort_kind"] = sort_kind
    if sort_dir:
        state["sort_dir"] = sort_dir
    if target:
        state["target"] = target
    if extra:
        state.update(extra)
    return state


async def build_tags_catalog_payload(
    ctx: CryptoContext,
    *,
    sort_kind: str = "count",
    sort_dir: str = "desc",
) -> list[dict[str, str | int]]:
    """Build the shared tags catalog payload for frontend consumers."""

    tag_service = get_tag_service()
    items = await tag_service.get_tags_index_items(
        ctx,
        sort_kind=tag_service.normalize_tags_sort_kind(sort_kind),
        sort_dir=tag_service.normalize_tags_sort_dir(sort_dir),
    )
    return [
        {"name": item.name, "hash": item.hash, "count": item.count} for item in items
    ]
