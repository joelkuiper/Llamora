"""Helpers for building per-entry LLM context payloads."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


logger = logging.getLogger(__name__)


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

