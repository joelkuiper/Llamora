"""Metadata generation helpers for assistant replies."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Mapping, Sequence

from emoji import emoji_count, is_emoji

from llamora.llm.client import LLMClient
from llamora.llm.prompt_templates import render_prompt_template


logger = logging.getLogger(__name__)


DEFAULT_METADATA_EMOJI = "🌳"


_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|```$", re.MULTILINE)


@lru_cache(maxsize=1)
def _metadata_system_prompt() -> str:
    """Return the cached system prompt used for metadata generation."""

    prompt = render_prompt_template("metadata_system.txt.j2")
    return prompt.strip()


def _strip_code_fence(text: str) -> str:
    """Remove Markdown code fences if present."""

    if "```" not in text:
        return text
    return _CODE_FENCE_RE.sub("", text).strip()


def _extract_json_object(text: str) -> Mapping[str, Any] | None:
    """Parse the first JSON object found in ``text`` if any."""

    cleaned = _strip_code_fence(text.strip())
    if not cleaned:
        return None

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first == -1 or last == -1 or last <= first:
            return None
        snippet = cleaned[first : last + 1]
        try:
            payload = json.loads(snippet)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, Mapping):
        return payload
    return None


def _normalise_keywords(value: Any, *, max_items: int = 3) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    keywords: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            continue
        candidate = raw.strip()
        if not candidate:
            continue
        candidate = candidate.lstrip("#")
        if not candidate:
            continue
        formatted = f"#{candidate}"[:64]
        lower = formatted.lower()
        if lower in seen:
            continue
        seen.add(lower)
        keywords.append(formatted)
        if len(keywords) >= max_items:
            break
    return keywords


def _clean_metadata_emoji(value: Any) -> str | None:
    """Return ``value`` if it is a single emoji, otherwise ``None``."""

    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate:
        return None

    if emoji_count(candidate) != 1:
        return None

    if not is_emoji(candidate):
        return None

    return candidate


def normalise_metadata_emoji(value: Any) -> str:
    """Validate ``value`` and return a usable emoji for metadata."""

    cleaned = _clean_metadata_emoji(value)
    if cleaned is not None:
        return cleaned
    return DEFAULT_METADATA_EMOJI


def _sanitise_metadata(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate ``payload`` and apply fallback values when necessary."""

    emoji = DEFAULT_METADATA_EMOJI
    keywords: list[str] = []

    if isinstance(payload, Mapping):
        emoji = normalise_metadata_emoji(payload.get("emoji"))
        keywords = _normalise_keywords(payload.get("keywords"))

    return {"emoji": emoji, "keywords": keywords}


def _metadata_json_schema() -> dict[str, Any]:
    """Return the JSON schema used to constrain metadata completions."""

    return {
        "type": "object",
        "properties": {
            "emoji": {"type": "string", "minLength": 1, "maxLength": 16},
            "keywords": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
                "maxItems": 5,
            },
        },
        "required": ["emoji", "keywords"],
        "additionalProperties": True,
    }


async def generate_metadata(
    llm: LLMClient,
    reply_text: str,
    *,
    request_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate metadata for ``reply_text`` using a second LLM pass."""

    reply = reply_text.strip()
    if not reply:
        return {"emoji": DEFAULT_METADATA_EMOJI, "keywords": []}

    system_prompt = _metadata_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": reply},
    ]

    schema = _metadata_json_schema()
    params = {
        "n_predict": 64,
        "json_schema": schema,
        "response_format": {"type": "json_schema", "schema": schema},
    }
    if request_overrides:
        params.update({k: v for k, v in request_overrides.items() if v is not None})

    try:
        raw = await llm.complete_chat(messages, params=params)
    except Exception:
        logger.exception("Metadata generation request failed")
        return {"emoji": DEFAULT_METADATA_EMOJI, "keywords": []}

    metadata = _extract_json_object(raw)
    if metadata is None:
        logger.debug("Metadata helper returned non-JSON payload: %r", raw)
    return _sanitise_metadata(metadata)


__all__ = ["generate_metadata", "normalise_metadata_emoji", "DEFAULT_METADATA_EMOJI"]
