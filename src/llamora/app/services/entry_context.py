"""Helpers for entry rendering and per-entry LLM context payloads."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from llamora.app.services.container import get_services
from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.services.session_context import get_session_context
from llamora.app.services.time import local_date, date_and_part
from datetime import date as date_cls


logger = logging.getLogger(__name__)


def _render_entries_markdown(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        entry_item = entry.get("entry")
        if isinstance(entry_item, dict):
            text = entry_item.get("text", "")
            entry_item["text_html"] = render_markdown_to_html(text)
        for response in entry.get("responses") or []:
            if isinstance(response, dict):
                text = response.get("text", "")
                response["text_html"] = render_markdown_to_html(text)


def _extract_tag_metadata(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return tag-related metadata available in ``meta``."""

    if not isinstance(meta, Mapping):
        return {}

    tag_metadata: dict[str, Any] = {}
    tags = meta.get("tags")
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes, bytearray)):
        cleaned = [str(item).strip() for item in tags if str(item).strip()]
        if cleaned:
            tag_metadata["tags"] = cleaned

    emoji = meta.get("emoji")
    if emoji:
        tag_metadata["emoji"] = str(emoji).strip()

    return tag_metadata


@dataclass(slots=True)
class EntryContext:
    """Payload container for a single user entry."""

    entry_id: str
    text: str
    tags: tuple[dict[str, Any], ...]
    tag_metadata: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "entry": {
                "id": self.entry_id,
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
    entry_id: str,
) -> dict[str, Any] | None:
    """Build a context payload for a single user entry."""

    entries = await db.entries.get_entries_by_ids(user_id, [entry_id], dek)
    if not entries:
        logger.info("Entry context skipped; entry %s not found", entry_id)
        return None

    entry = entries[0]
    text = str(entry.get("text") or "").strip()
    tags = await db.tags.get_tags_for_entry(user_id, entry_id, dek)
    tag_metadata = _extract_tag_metadata(entry.get("meta"))
    payload = EntryContext(
        entry_id=str(entry.get("id") or entry_id),
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
    entries = await services.db.entries.get_entries_for_date(user["id"], date, dek)
    _render_entries_markdown(entries)

    today_date = local_date()
    today = today_date.isoformat()
    min_date = await services.db.entries.get_first_entry_date(user["id"]) or today
    is_today = date == today
    is_future = False
    try:
        is_future = date_cls.fromisoformat(date) > today_date
    except ValueError:
        is_future = False
    pending_entry_id = None

    opening_entries: list[dict[str, Any]] = []
    regular_entries: list[dict[str, Any]] = []
    for item in entries:
        entry_item = item.get("entry", {}) if isinstance(item, dict) else {}
        meta = entry_item.get("meta", {}) if isinstance(entry_item, dict) else {}
        if meta.get("auto_opening"):
            opening_entries.append(item)
        else:
            regular_entries.append(item)

    entries = [*opening_entries, *regular_entries]
    opening_stream = is_today and not opening_entries

    return {
        "entries": entries,
        "pending_entry_id": pending_entry_id,
        "is_today": is_today,
        "opening_stream": opening_stream,
        "min_date": min_date,
        "is_future": is_future,
    }


def build_llm_context(
    *,
    user_time: str | None,
    tz_cookie: str | None,
    entry_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the LLM context payload shared across response flows."""

    timestamp = user_time or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    tz = tz_cookie or "UTC"
    date_str, part = date_and_part(timestamp, tz)
    ctx: dict[str, Any] = {"date": date_str, "part_of_day": part}
    if entry_context:
        ctx.update(entry_context)
    return ctx
