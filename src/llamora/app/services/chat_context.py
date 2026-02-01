"""Utilities for constructing chat rendering context."""

from __future__ import annotations

from typing import Any, Mapping

from llamora.app.services.container import get_services
from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import local_date


async def get_chat_context(
    user: Mapping[str, Any],
    date: str,
) -> dict[str, Any]:
    """Return shared chat rendering context details for ``user`` on ``date``.

    The returned dictionary contains:
    - ``history``: the conversation history for the requested date
    - ``pending_msg_id``: reserved for explicit stream resumes (unused by default)
    - ``is_today``: whether the requested date matches the user's current local day
    - ``opening_stream``: whether the chat should initiate the opening message stream
    """

    services = get_services()
    dek = await get_session_context().require_dek()
    history = await services.db.messages.get_history(user["id"], date, dek)

    for entry in history:
        message = entry.get("message", "") if isinstance(entry, dict) else ""
        entry["message_html"] = render_markdown_to_html(message)

    today = local_date().isoformat()
    is_today = date == today
    pending_msg_id = None
    opening_stream = False

    return {
        "history": history,
        "pending_msg_id": pending_msg_id,
        "is_today": is_today,
        "opening_stream": opening_stream,
    }
