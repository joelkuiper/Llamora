"""Utilities for constructing chat rendering context."""

from __future__ import annotations

from typing import Any, Mapping

from llamora.app.services.auth_helpers import get_secure_cookie_manager
from llamora.app.services.container import get_services
from llamora.app.services.history_serializer import serialize_history_for_view
from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.services.time import get_timezone, local_date


async def get_chat_context(
    user: Mapping[str, Any],
    date: str,
) -> dict[str, Any]:
    """Return shared chat rendering context details for ``user`` on ``date``.

    The returned dictionary contains:
    - ``history``: the conversation history for the requested date
    - ``pending_msg_id``: the most recent user message awaiting a reply, if any
    - ``is_today``: whether the requested date matches the user's current local day
    - ``opening_stream``: whether the chat should initiate the opening message stream
    """

    services = get_services()
    manager = get_secure_cookie_manager()
    dek = manager.get_dek()
    if dek is None:
        raise RuntimeError("Missing encryption key for chat context")
    history = await services.db.messages.get_history(user["id"], date, dek)

    for entry in history:
        message = entry.get("message", "") if isinstance(entry, dict) else ""
        entry["message_html"] = render_markdown_to_html(message)

    today = local_date().isoformat()
    is_today = date == today
    pending_msg_id = None
    if history and history[-1].get("role") == "user":
        pending_msg_id = history[-1].get("id")

    opening_stream = not history and is_today

    tz = get_timezone()
    rendered_history = serialize_history_for_view(history, tz)

    return {
        "history": rendered_history,
        "pending_msg_id": pending_msg_id,
        "is_today": is_today,
        "opening_stream": opening_stream,
    }
