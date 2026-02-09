from __future__ import annotations

import logging
import re
from typing import Any, Iterable

import orjson

from llamora.llm.prompt_templates import render_prompt_template

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _day_summary_system_prompt(entry_count: int) -> str:
    return render_prompt_template("day_summary_system.txt.j2", entry_count=entry_count)


def _day_summary_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "day_summary",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "minLength": 24,
                        "maxLength": 360,
                    },
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    }


def _clean_summary(raw: str) -> str:
    if not raw:
        return ""
    text = " ".join(str(raw).split()).strip()
    text = text.strip('"').strip("'").strip()
    if not text:
        return ""
    sentences = [seg for seg in _SENTENCE_SPLIT.split(text) if seg]
    if len(sentences) > 3:
        text = " ".join(sentences[:3])
    if len(text) > 420:
        text = text[:420].rsplit(" ", 1)[0].rstrip()
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


def _normalize_entry_text(text: str) -> str:
    cleaned = " ".join(str(text).split()).strip()
    if len(cleaned) > 280:
        cleaned = cleaned[:280].rsplit(" ", 1)[0].rstrip()
        if cleaned:
            cleaned += "…"
    return cleaned


def _build_user_prompt(date: str, entries: Iterable[dict]) -> str:
    lines = [f"Day: {date}", "Entries:"]
    for entry in entries:
        role = str(entry.get("role") or "unknown").strip().title()
        text = _normalize_entry_text(entry.get("text", ""))
        if not text:
            continue
        lines.append(f"- {role}: {text}")
    return "\n".join(lines)


async def generate_day_summary(
    llm,
    date: str,
    entries: Iterable[dict],
) -> str:
    entry_list = [entry for entry in entries if entry.get("text")]
    if not entry_list:
        return ""

    system_prompt = _day_summary_system_prompt(len(entry_list))
    user_prompt = _build_user_prompt(date, entry_list)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    params = {
        "temperature": 0.2,
        "n_predict": 140,
        "response_format": _day_summary_response_format(),
    }

    try:
        raw = await llm.complete_messages(messages, params=params)
    except Exception:
        logger.exception("Day summary request failed")
        return ""

    summary = _extract_summary(raw)
    if summary:
        return summary

    retry_messages = [
        {
            "role": "system",
            "content": (
                _day_summary_system_prompt(len(entry_list))
                + "\nReturn 1-3 plain sentences. Avoid headings or timestamps."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]
    try:
        raw_retry = await llm.complete_messages(retry_messages, params=params)
    except Exception:
        logger.exception("Day summary retry failed")
        return summary

    return _extract_summary(raw_retry) or summary


__all__ = ["generate_day_summary"]
