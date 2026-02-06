from __future__ import annotations

import logging
import re
from typing import Any, Sequence

import orjson

from llamora.llm.prompt_templates import render_prompt_template

from .tag_service import TagEntryPreview


logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _tag_summary_system_prompt() -> str:
    return render_prompt_template("tag_summary_system.txt.j2")

def _tag_summary_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "tag_summary",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    }


def _build_user_prompt(
    tag_name: str,
    entry_count: int,
    last_used: str | None,
    samples: Sequence[TagEntryPreview],
) -> str:
    lines = [
        f"Tag: {tag_name}",
        f"Entry count: {entry_count}",
        f"Last used: {last_used or 'unknown'}",
        "Recent snippets (most recent first):",
    ]
    for sample in samples:
        lines.append(f"- {sample.created_at}: {sample.preview}")
    return "\n".join(lines)


def _clean_summary(raw: str) -> str:
    if not raw:
        return ""
    text = " ".join(str(raw).split()).strip()
    text = text.strip('"').strip("'").strip()
    if not text:
        return ""
    sentences = [seg for seg in _SENTENCE_SPLIT.split(text) if seg]
    if len(sentences) > 2:
        text = " ".join(sentences[:2])
    if len(text) > 260:
        text = text[:260].rsplit(" ", 1)[0].rstrip()
        if text:
            text += "..."
    return text

def _extract_summary(raw: str) -> str:
    if not raw:
        return ""
    try:
        parsed = orjson.loads(raw)
    except Exception:
        return _clean_summary(raw)
    if isinstance(parsed, dict):
        summary = parsed.get("summary")
        if isinstance(summary, str):
            return _clean_summary(summary)
    return _clean_summary(raw)


async def generate_tag_summary(
    llm,
    tag_name: str,
    entry_count: int,
    last_used: str | None,
    samples: Sequence[TagEntryPreview],
) -> str:
    if not tag_name or not samples:
        return ""

    system_prompt = _tag_summary_system_prompt()
    user_prompt = _build_user_prompt(tag_name, entry_count, last_used, samples)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw = await llm.complete_messages(
            messages,
            params={
                "temperature": 0.3,
                "n_predict": 120,
                "response_format": _tag_summary_response_format(),
            },
        )
    except Exception:
        logger.exception("Tag summary request failed")
        return ""

    return _extract_summary(raw)


__all__ = ["generate_tag_summary"]
