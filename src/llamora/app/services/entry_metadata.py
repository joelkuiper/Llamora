"""Metadata generation helpers for tag suggestions."""

from __future__ import annotations

import logging
from typing import Any, Mapping

import orjson

from llamora.llm.prompt_templates import render_prompt_template


logger = logging.getLogger(__name__)

DEFAULT_METADATA_EMOJI = "🌳"


def _metadata_system_prompt() -> str:
    """Return the cached system prompt used for metadata generation."""

    return render_prompt_template("metadata_system.txt.j2")


def _metadata_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "entry_metadata",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "emoji": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["emoji", "tags"],
                "additionalProperties": False,
            },
        },
    }


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = orjson.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    depth = 0
    start = None
    for idx, ch in enumerate(text):
        if ch == "{":
            if start is None:
                start = idx
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                snippet = text[start : idx + 1]
                try:
                    parsed = orjson.loads(snippet)
                    return parsed if isinstance(parsed, dict) else None
                except Exception:
                    return None
    return None


def _sanitise_metadata(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"emoji": DEFAULT_METADATA_EMOJI, "tags": []}

    emoji = payload.get("emoji") or DEFAULT_METADATA_EMOJI
    if not isinstance(emoji, str) or not emoji.strip():
        emoji = DEFAULT_METADATA_EMOJI

    tags = payload.get("tags")
    if not isinstance(tags, list):
        tags = []
    else:
        tags = [str(item).strip() for item in tags if str(item).strip()]

    return {"emoji": emoji, "tags": tags}


async def generate_metadata(llm, text: str) -> dict[str, Any]:
    """Generate metadata for ``text`` using a single LLM pass."""

    if not text or not str(text).strip():
        return {"emoji": DEFAULT_METADATA_EMOJI, "tags": []}

    system_prompt = _metadata_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": str(text)},
    ]

    try:
        raw = await llm.complete_messages(
            messages,
            params={
                "temperature": 0.2,
                "n_predict": 140,
                "response_format": _metadata_response_format(),
            },
        )
    except Exception:
        logger.exception("Metadata generation request failed")
        return {"emoji": DEFAULT_METADATA_EMOJI, "tags": []}

    metadata = _extract_json_object(raw)
    if metadata is None:
        logger.debug("Metadata helper returned non-JSON payload: %r", raw)
    return _sanitise_metadata(metadata)


__all__ = ["generate_metadata", "DEFAULT_METADATA_EMOJI"]
