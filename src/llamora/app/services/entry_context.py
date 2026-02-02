"""Helpers for entry rendering and per-entry LLM context payloads."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from llamora.app.services.container import get_services
from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import local_date


logger = logging.getLogger(__name__)


def _render_entries_markdown(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        message = entry.get("message")
        if isinstance(message, dict):
            text = message.get("message", "")
            message["message_html"] = render_markdown_to_html(text)
        for reply in entry.get("replies") or []:
            if isinstance(reply, dict):
                text = reply.get("message", "")
                reply["message_html"] = render_markdown_to_html(text)


def _extract_tag_metadata(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return tag-related metadata available in ``meta``."""

    if not isinstance(meta, Mapping):
        return {}

    tag_metadata: dict[str, Any] = {}
    keywords = meta.get("keywords")
    if isinstance(keywords, Sequence) and not isinstance(
        keywords, (str, bytes, bytearray)
    ):
        cleaned = [str(item).strip() for item in keywords if str(item).strip()]
        if cleaned:
            tag_metadata["keywords"] = cleaned

    emoji = meta.get("emoji")
    if emoji:
        tag_metadata["emoji"] = str(emoji).strip()

    return tag_metadata


@dataclass(slots=True)
class EntryContext:
    """Payload container for a single user entry."""

    message_id: str
    text: str
    tags: tuple[dict[str, Any], ...]
    tag_metadata: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "entry": {
                "id": self.message_id,
                "text": self.text,
                "tags": [dict(tag) for tag in self.tags],
                "tag_metadata": dict(self.tag_metadata),
            }
        }


async def build_entry_context(
    db,
    user_id: str,
    dek: bytes,
    *,
    user_msg_id: str,
) -> dict[str, Any] | None:
    """Build a context payload for a single user entry."""

    messages = await db.messages.get_messages_by_ids(user_id, [user_msg_id], dek)
    if not messages:
        logger.info("Entry context skipped; message %s not found", user_msg_id)
        return None

    message = messages[0]
    text = str(message.get("message") or "").strip()
    tags = await db.tags.get_tags_for_message(user_id, user_msg_id, dek)
    tag_metadata = _extract_tag_metadata(message.get("meta"))
    payload = EntryContext(
        message_id=str(message.get("id") or user_msg_id),
        text=text,
        tags=tuple(tags),
        tag_metadata=tag_metadata,
    )
    return payload.to_payload()


async def get_entries_context(
    user: Mapping[str, Any],
    date: str,
) -> dict[str, Any]:
    """Return shared entry rendering context details for ``user`` on ``date``."""

    services = get_services()
    dek = await get_session_context().require_dek()
    entries = await services.db.messages.get_history(user["id"], date, dek)
    _render_entries_markdown(entries)

    today = local_date().isoformat()
    is_today = date == today
    pending_msg_id = None
    opening_stream = False

    return {
        "entries": entries,
        "pending_msg_id": pending_msg_id,
        "is_today": is_today,
        "opening_stream": opening_stream,
    }
