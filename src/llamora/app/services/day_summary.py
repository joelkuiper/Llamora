from __future__ import annotations

import logging
import re
from typing import Any, Iterable

import orjson

from llamora.llm.prompt_templates import render_prompt_template

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_FORBIDDEN_RE = re.compile(
    r"\b(assistant|ai|model|response|today|yesterday|tonight|this morning|this afternoon|this evening)\b",
    re.IGNORECASE,
)


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
    if text and text[-1] not in ".!?":
        trimmed = text.rsplit(" ", 1)[0].rstrip()
        if trimmed and trimmed != text:
            text = f"{trimmed}..."
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


def _contains_forbidden(text: str) -> bool:
    return bool(_FORBIDDEN_RE.search(text or ""))


def _strip_forbidden_sentences(text: str) -> str:
    if not text:
        return ""
    sentences = [seg for seg in _SENTENCE_SPLIT.split(text) if seg]
    kept = [seg for seg in sentences if not _contains_forbidden(seg)]
    return _clean_summary(" ".join(kept)) if kept else ""


def _normalize_entry_text(text: str) -> str:
    cleaned = " ".join(str(text).split()).strip()
    if len(cleaned) > 280:
        cleaned = cleaned[:280].rsplit(" ", 1)[0].rstrip()
        if cleaned:
            cleaned += "…"
    return cleaned


def _build_user_prompt(_date: str, entries: Iterable[dict]) -> str:
    lines = ["Entries:"]
    for entry in entries:
        role = str(entry.get("role") or "").strip().lower()
        if role and role != "user" and not _is_opening_entry(entry):
            continue
        text = _normalize_entry_text(entry.get("text", ""))
        if not text:
            continue
        lines.append(f"- {text}")
    return "\n".join(lines)


def _is_user_entry(entry: dict) -> bool:
    return str(entry.get("role") or "").strip().lower() == "user"


def _is_opening_entry(entry: dict) -> bool:
    meta = entry.get("meta") or {}
    return bool(meta.get("auto_opening"))


async def generate_day_summary(
    llm,
    date: str,
    entries: Iterable[dict],
) -> str:
    opening_entries = [
        entry for entry in entries if entry.get("text") and _is_opening_entry(entry)
    ]
    user_entries = [
        entry for entry in entries if entry.get("text") and _is_user_entry(entry)
    ]
    entry_list = [*opening_entries, *user_entries]
    if not entry_list:
        return "No entries recorded for this day."

    system_prompt = _day_summary_system_prompt(len(entry_list))
    user_prompt = _build_user_prompt(date, entry_list)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    params = {
        "temperature": 0.2,
        "n_predict": 220,
        "response_format": _day_summary_response_format(),
    }

    try:
        raw = await llm.complete_messages(messages, params=params)
    except Exception:
        logger.exception("Day summary request failed")
        return ""

    summary = _extract_summary(raw)
    if summary and not _contains_forbidden(summary):
        return summary

    retry_messages = [
        {
            "role": "system",
            "content": (
                _day_summary_system_prompt(len(entry_list))
                + "\nReturn 1-3 plain sentences. Avoid headings or timestamps."
                " Do not mention the assistant, AI, model, or responses."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]
    try:
        raw_retry = await llm.complete_messages(retry_messages, params=params)
    except Exception:
        logger.exception("Day summary retry failed")
        return summary

    retry_summary = _extract_summary(raw_retry)
    if retry_summary and not _contains_forbidden(retry_summary):
        return retry_summary
    cleaned = _strip_forbidden_sentences(retry_summary or summary)
    return cleaned or summary


__all__ = ["generate_day_summary"]
