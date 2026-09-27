"""Metadata generation helpers for tag suggestions."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Mapping

import orjson

from llamora.llm.entry_template import IMAGE_REF
from llamora.llm.prompt_templates import render_prompt_template
from llamora.app.util.tags import canonicalize


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
                    "tags": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                    },
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
        cleaned: list[str] = []
        for item in tags:
            raw = str(item or "").strip()
            if not raw:
                continue
            try:
                cleaned.append(canonicalize(raw))
            except ValueError:
                continue
        tags = cleaned

    return {"emoji": emoji, "tags": tags}


async def generate_metadata(
    llm,
    text: str,
    *,
    image_ids: Sequence[str] = (),
    image_resolver: Any = None,
) -> dict[str, Any]:
    """Generate metadata (emoji and tags) for an entry in a single LLM pass.

    The entry's images are shown to the model too when it may see them (the
    ``LLM.vision`` policy) and a resolver is given, so an entry of only
    images still gets suggestions.
    """

    text = str(text or "").strip()
    policy = getattr(llm, "image_policy", None)
    ids: list[str] = []
    if image_ids and image_resolver is not None and policy is not None and policy.send:
        ids = [str(image_id) for image_id in image_ids][: policy.max_images]
    if not text and not ids:
        return {"emoji": DEFAULT_METADATA_EMOJI, "tags": []}

    content: str | list[dict[str, Any]] = text
    if ids:
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        parts.extend({"type": IMAGE_REF, "image_id": image_id} for image_id in ids)
        content = parts

    system_prompt = _metadata_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    try:
        raw = await llm.complete_messages(
            messages,
            params={
                "temperature": 0.2,
                "n_predict": 140,
                "response_format": _metadata_response_format(),
            },
            image_resolver=image_resolver if ids else None,
        )
    except Exception:
        logger.exception("Metadata generation request failed")
        return {"emoji": DEFAULT_METADATA_EMOJI, "tags": []}

    metadata = _extract_json_object(raw)
    if metadata is None:
        logger.debug("Metadata helper returned non-JSON payload: %r", raw)
    return _sanitise_metadata(metadata)


__all__ = ["generate_metadata", "DEFAULT_METADATA_EMOJI"]
