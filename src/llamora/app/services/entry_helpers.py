"""Helpers for entry routes and streaming logic."""

from __future__ import annotations

import math
import logging
from contextlib import suppress
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence
from dataclasses import dataclass

import orjson
from quart import Response
from werkzeug.datastructures import Headers

from llamora.app.services.tag_recall import TagRecallContext


logger = logging.getLogger(__name__)


def replace_newline(value: str) -> str:
    """Normalize newline characters for SSE payloads."""

    return (
        value.replace("\r\n", "[newline]")
        .replace("\r", "[newline]")
        .replace("\n", "[newline]")
    )


def _serialize_payload(payload: Any) -> tuple[str, bool]:
    """Serialize a payload for SSE transmission."""

    if payload is None:
        return "", True

    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")

    if isinstance(payload, str):
        return payload, True

    if isinstance(payload, Mapping) or isinstance(payload, Sequence):
        try:
            serialized = orjson.dumps(payload).decode()
        except TypeError:
            if isinstance(payload, Mapping):
                serialized = "{}"
            else:
                serialized = "[]"
        return serialized, False

    return str(payload), True


def format_sse_event(event_type: str, payload: Any) -> str:
    """Format a Server-Sent Event payload with newline normalization."""

    serialized, _ = _serialize_payload(payload)
    if serialized:
        serialized = replace_newline(serialized)
    return f"event: {event_type}\ndata: {serialized}\n\n"


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


def apply_response_kind_prompt(
    history: Sequence[Mapping[str, Any]],
    response_prompt: str | None,
) -> list[dict[str, Any]]:
    """Append a response-kind system prompt to history for LLM generation."""

    normalized = [dict(entry) for entry in history]
    if not response_prompt:
        return normalized

    normalized.append({"role": "system", "text": str(response_prompt).strip()})
    return normalized


def build_entry_history(
    entries: Sequence[Mapping[str, Any]], entry_id: str
) -> list[dict[str, Any]]:
    """Flatten entry aggregates into a linear history up to ``entry_id``."""

    history: list[dict[str, Any]] = []
    target_id = str(entry_id)
    for entry in entries:
        entry_item = entry.get("entry")
        if isinstance(entry_item, Mapping):
            history.append(dict(entry_item))
            if str(entry_item.get("id")) == target_id:
                return history
        for response in entry.get("responses") or []:
            if isinstance(response, Mapping):
                history.append(dict(response))
    return history


async def start_stream_session(
    *,
    manager,
    entry_id: str,
    uid: str,
    date: str,
    history: list[dict],
    dek: bytes,
    params: dict | None = None,
    context: dict | None = None,
    reply_to: str | None = None,
    meta_extra: dict | None = None,
    use_default_reply_to: bool = True,
):
    pending = manager.get(entry_id, uid)
    if pending:
        return pending
    return manager.start_stream(
        entry_id,
        uid,
        date,
        history,
        dek,
        params,
        context,
        reply_to=reply_to,
        meta_extra=meta_extra,
        use_default_reply_to=use_default_reply_to,
    )


@dataclass(slots=True)
class RecallAugmentation:
    """Result of applying recall context to an entry history."""

    messages: list[dict[str, Any]]
    recall_inserted: bool
    recall_index: int | None


def _locate_recall_entry(
    messages: Sequence[Mapping[str, Any]],
    *,
    text_key: str,
    recall_text: str,
) -> int | None:
    for idx, entry in enumerate(messages):
        if entry.get("role") != "system":
            continue
        if str(entry.get(text_key) or "") == recall_text:
            return idx
    return None


def _normalize_recall_tags(tags: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for tag in tags:
        clean = str(tag or "").strip()
        if clean:
            normalized.append(clean)
    return tuple(normalized)


def history_has_tag_recall(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    tags: Sequence[str],
    date: str | None,
) -> bool:
    """Return ``True`` when ``history`` already contains matching tag recall."""

    normalized_tags = _normalize_recall_tags(tags)
    if not normalized_tags:
        return False

    date_key = str(date or "").strip()

    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("role") != "system":
            continue
        meta = entry.get("meta")
        if not isinstance(meta, Mapping):
            continue
        tag_meta = meta.get("tag_recall")
        if not isinstance(tag_meta, Mapping):
            continue
        entry_tags = tag_meta.get("tags")
        if not isinstance(entry_tags, Sequence):
            continue
        normalized_entry_tags = _normalize_recall_tags(entry_tags)
        if normalized_entry_tags != normalized_tags:
            continue
        if date_key:
            entry_date = str(tag_meta.get("date") or "").strip()
            if entry_date != date_key:
                continue
        return True

    return False


async def augment_history_with_recall(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    recall_context: TagRecallContext | None,
    *,
    llm_client,
    params: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    target_entry_id: str | None = None,
    include_tag_metadata: bool = False,
    tag_recall_date: str | None = None,
) -> RecallAugmentation:
    """Insert ``recall_context`` into ``history`` and optionally trim it."""

    logger = logging.getLogger(__name__)

    augmented: list[dict[str, Any]] = [dict(entry) for entry in history]
    recall_inserted = False
    recall_index: int | None = None
    recall_entry: dict[str, Any] | None = None

    if recall_context:
        recall_entry = {
            "id": None,
            "role": "system",
            "text": recall_context.text,
        }
        if include_tag_metadata:
            tag_meta: dict[str, Any] = {"tags": list(recall_context.tags)}
            if tag_recall_date:
                tag_meta["date"] = str(tag_recall_date)
            recall_entry["meta"] = {"tag_recall": tag_meta}
            tag_items = [
                {"name": tag} for tag in recall_context.tags if str(tag or "").strip()
            ]
            if tag_items:
                recall_entry["tags"] = tag_items

        if recall_entry is not None:
            augmented = []
            for entry in history:
                entry_dict = dict(entry)
                if (
                    not recall_inserted
                    and target_entry_id is not None
                    and str(entry_dict.get("id")) == str(target_entry_id)
                ):
                    recall_index = len(augmented)
                    augmented.append(dict(recall_entry))
                    recall_inserted = True
                augmented.append(entry_dict)

            if not recall_inserted:
                recall_index = len(augmented)
                augmented.append(dict(recall_entry))
                recall_inserted = True

        logger.debug(
            "Inserted tag recall for entry=%s tags=%s inserted=%s text=%s",
            target_entry_id,
            recall_context.tags,
            recall_inserted,
            recall_context.text[:300],
        )

    trimmed_history = augmented
    if llm_client is not None:
        try:
            trimmed_history = await llm_client.trim_history(
                augmented, params=params, context=context
            )
        except Exception:
            logger.exception("Failed to trim history with recall context")

    if recall_context and recall_entry is not None:
        recall_index = _locate_recall_entry(
            trimmed_history, text_key="text", recall_text=recall_context.text
        )
        recall_inserted = recall_index is not None
    else:
        recall_index = None
        recall_inserted = False

    return RecallAugmentation(
        messages=list(trimmed_history),
        recall_inserted=recall_inserted,
        recall_index=recall_index,
    )


async def augment_opening_with_recall(
    messages: Sequence[Mapping[str, Any] | dict[str, Any]],
    recall_context: TagRecallContext | None,
    *,
    llm_client,
    params: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    insert_index: int | None = None,
    include_tag_metadata: bool = False,
    tag_recall_date: str | None = None,
) -> RecallAugmentation:
    """Insert recall context into opening messages using ``content`` keys."""

    augmented: list[dict[str, Any]] = [dict(entry) for entry in messages]
    recall_inserted = False
    recall_index: int | None = None
    recall_entry: dict[str, Any] | None = None

    if recall_context:
        recall_entry = {
            "id": None,
            "role": "system",
            "content": recall_context.text,
        }
        if include_tag_metadata:
            tag_meta: dict[str, Any] = {"tags": list(recall_context.tags)}
            if tag_recall_date:
                tag_meta["date"] = str(tag_recall_date)
            recall_entry["meta"] = {"tag_recall": tag_meta}
            tag_items = [
                {"name": tag} for tag in recall_context.tags if str(tag or "").strip()
            ]
            if tag_items:
                recall_entry["tags"] = tag_items

    if recall_entry is not None:
        if insert_index is not None:
            bounded_index = max(0, min(insert_index, len(augmented)))
            augmented.insert(bounded_index, dict(recall_entry))
            recall_index = bounded_index
            recall_inserted = True
        else:
            recall_index = len(augmented)
            augmented.append(dict(recall_entry))
            recall_inserted = True

    trimmed_history = augmented
    if llm_client is not None:
        try:
            trimmed_history = await llm_client.trim_history(
                augmented, params=params, context=context
            )
        except Exception:
            logger.exception("Failed to trim opening messages with recall context")

    if recall_context and recall_entry is not None:
        recall_index = _locate_recall_entry(
            trimmed_history, text_key="content", recall_text=recall_context.text
        )
        recall_inserted = recall_index is not None
    else:
        recall_index = None
        recall_inserted = False

    return RecallAugmentation(
        messages=list(trimmed_history),
        recall_inserted=recall_inserted,
        recall_index=recall_index,
    )


def _error_events(pending_response, chunk: str | None = None):
    """Yield SSE events for an errored streaming response."""

    message = pending_response.text or chunk or pending_response.error_message or ""
    yield format_sse_event("error", message)
    yield format_sse_event("done", {})


class StreamSession(Response):
    """Facade for Server-Sent Event responses.

    ``StreamSession`` encapsulates the boilerplate required to stream entry
    responses over SSE.  It standardises headers, formatting, and pending
    response lifecycle management so callers only need to select the desired
    flavour of stream.
    """

    _SSE_HEADERS = MappingProxyType(
        {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )

    def __init__(
        self,
        body,
        *,
        status: int = 200,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        merged_headers = Headers(headers or {})
        for key, value in self._SSE_HEADERS.items():
            if key not in merged_headers:
                merged_headers[key] = value
        super().__init__(
            body, status=status, mimetype="text/event-stream", headers=merged_headers
        )

    @classmethod
    def pending(cls, pending_response) -> "StreamSession":
        """Create a streaming response for an in-flight pending reply."""

        async def _body():
            try:
                async for event in cls._stream_pending(pending_response):
                    yield event
            finally:
                if not pending_response.done and not pending_response.cancelled:
                    with suppress(Exception):
                        await pending_response.cancel()

        return cls(_body())

    @classmethod
    def saved(cls, message: Mapping[str, Any]) -> "StreamSession":
        """Create a streaming response for a saved assistant message."""

        async def _body():
            async for event in cls._stream_saved(message):
                yield event

        return cls(_body())

    @classmethod
    def error(cls, message: Any) -> "StreamSession":
        """Create a streaming response for an error payload."""

        return cls(cls._error_stream(message))

    @classmethod
    def backpressure(cls, message: Any, retry_after: float | int) -> "StreamSession":
        """Create an error stream that advertises a retry delay."""

        retry_seconds = max(1, int(math.ceil(float(retry_after))))
        headers = {"Retry-After": str(retry_seconds)}
        return cls(
            cls._error_stream(message),
            status=429,
            headers=headers,
        )

    @classmethod
    def raw(cls, payload: str) -> "StreamSession":
        """Create a streaming response from a pre-formatted SSE payload."""

        return cls(payload)

    @staticmethod
    async def _stream_saved(message: Mapping[str, Any]):
        yield format_sse_event("message", message.get("text", ""))
        yield format_sse_event("done", {"assistant_entry_id": message.get("id")})

    @staticmethod
    async def _stream_pending(pending_response):
        async for chunk in pending_response.stream():
            if pending_response.error:
                for event in _error_events(pending_response, chunk):
                    yield event
                return

            if chunk:
                yield format_sse_event("message", chunk)

        if pending_response.error:
            for event in _error_events(pending_response):
                yield event
            return

        if pending_response.meta is not None:
            yield format_sse_event("meta", pending_response.meta)

        yield format_sse_event(
            "done", {"assistant_entry_id": pending_response.assistant_entry_id}
        )

    @staticmethod
    async def _error_stream(message: Any):
        yield format_sse_event("error", message)
        yield format_sse_event("done", {})
