"""Helpers for chat routes and streaming logic."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Iterable, Mapping, Sequence

import orjson

from app.services.time import date_and_part


def replace_newline(value: str) -> str:
    """Normalize newline characters for SSE payloads."""

    return (
        value.replace("\r\n", "[newline]")
        .replace("\r", "[newline]")
        .replace("\n", "[newline]")
    )


def find_existing_assistant_reply(
    history: Sequence[Mapping[str, Any]], user_msg_id: str
) -> Mapping[str, Any] | None:
    """Locate an assistant reply paired with ``user_msg_id`` in ``history``."""

    expect_adjacent_reply = False

    for message in history:
        if (
            message.get("reply_to") == user_msg_id
            and message.get("role") == "assistant"
        ):
            return message

        if expect_adjacent_reply:
            if message.get("role") == "assistant":
                return message
            expect_adjacent_reply = False

        expect_adjacent_reply = message.get("id") == user_msg_id

    return None


async def locate_message_and_reply(
    db,
    user_id: str,
    dek: str,
    date: str,
    user_msg_id: str,
):
    """Fetch history containing ``user_msg_id`` and any existing reply.

    Returns a tuple of ``(history, assistant_message, actual_date)``. ``history`` is
    the conversation history that includes the user message. ``assistant_message``
    is ``None`` when no reply has been stored yet. ``actual_date`` reflects the
    conversation date associated with ``history`` and may differ from the input
    ``date`` when the message resides on another day.
    """

    message_info = await db.messages.get_message_with_reply(user_id, user_msg_id)
    actual_date = (message_info or {}).get("created_date") or date

    history = await db.messages.get_history(user_id, actual_date, dek)

    messages_by_id = {message.get("id"): message for message in history}
    user_message = messages_by_id.get(user_msg_id)
    if not user_message:
        return [], None, actual_date

    assistant_message = None
    if message_info and message_info.get("reply_id"):
        assistant_message = messages_by_id.get(message_info["reply_id"])

    if assistant_message is None:
        assistant_message = find_existing_assistant_reply(history, user_msg_id)

    return history, assistant_message, actual_date


def normalize_llm_config(
    raw_config: str | None, allowed_keys: Iterable[str] | None
) -> Mapping[str, Any] | None:
    """Validate and filter client-supplied LLM configuration parameters."""

    if not raw_config:
        return None

    try:
        parsed = orjson.loads(raw_config)
    except Exception:
        return None

    if not isinstance(parsed, dict):
        return None

    allowed = set(allowed_keys or [])
    filtered = {key: parsed[key] for key in parsed if key in allowed}

    return filtered or None


def build_conversation_context(
    user_time: str | None, tz_cookie: str | None
) -> Mapping[str, str]:
    """Compute contextual metadata for downstream LLM prompts."""

    timestamp = user_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    tz = tz_cookie or "UTC"
    date_str, part = date_and_part(timestamp, tz)
    return {"date": date_str, "part_of_day": part}


async def stream_saved_reply(message: Mapping[str, Any]):
    """Yield SSE events for an already-saved assistant message."""

    escaped_message = replace_newline(escape(message.get("message", "")))
    yield f"event: message\ndata: {escaped_message}\n\n"

    payload = orjson.dumps({"assistant_msg_id": message.get("id")}).decode()
    yield f"event: done\ndata: {escape(payload)}\n\n"


def _error_events(pending_response, chunk: str | None = None):
    """Yield SSE events for an errored streaming response."""

    message = chunk or pending_response.text or pending_response.error_message or ""
    formatted = replace_newline(escape(message)) if message else ""
    yield f"event: error\ndata: {formatted}\n\n"
    yield "event: done\ndata: {}\n\n"


async def stream_pending_reply(pending_response):
    """Stream chunks for an active LLM response using the SSE contract."""

    async for chunk in pending_response.stream():
        if pending_response.error:
            for event in _error_events(pending_response, chunk):
                yield event
            return

        if chunk:
            formatted_chunk = replace_newline(escape(chunk))
            yield f"event: message\ndata: {formatted_chunk}\n\n"

    if pending_response.error:
        for event in _error_events(pending_response):
            yield event
        return

    if pending_response.meta is not None:
        try:
            meta_payload = orjson.dumps(pending_response.meta).decode()
        except TypeError:
            meta_payload = "{}"
        safe_meta = replace_newline(escape(meta_payload, quote=False))
        yield f"event: meta\ndata: {safe_meta}\n\n"

    payload = orjson.dumps(
        {"assistant_msg_id": pending_response.assistant_msg_id}
    ).decode()
    safe_payload = replace_newline(escape(payload, quote=False))
    yield f"event: done\ndata: {safe_payload}\n\n"
