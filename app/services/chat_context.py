"""Utilities for constructing chat rendering context."""

from __future__ import annotations

from typing import Any, Mapping

from app import db
from app.services.auth_helpers import get_dek
from app.services.time import local_date


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

    dek = get_dek()
    history = await db.messages.get_history(user["id"], date, dek)

    today = local_date().isoformat()
    is_today = date == today
    pending_msg_id = None
    if history and history[-1].get("role") == "user":
        pending_msg_id = history[-1].get("id")

    opening_stream = not history and is_today

    return {
        "history": history,
        "pending_msg_id": pending_msg_id,
        "is_today": is_today,
        "opening_stream": opening_stream,
    }
