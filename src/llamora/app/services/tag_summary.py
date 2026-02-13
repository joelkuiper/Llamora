from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

from cachetools import TTLCache

import orjson

from llamora.llm.prompt_templates import render_prompt_template

from .tag_service import TagEntryPreview


logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SUMMARY_CACHE_TTL = 6 * 60 * 60
_SUMMARY_CACHE_MAX = 512
_SUMMARY_CACHE: TTLCache[str, "TagSummaryState"] = TTLCache(
    maxsize=_SUMMARY_CACHE_MAX, ttl=_SUMMARY_CACHE_TTL
)


@dataclass(slots=True)
class TagSummaryState:
    summary: str
    last_entry_id: str | None
    count: int
    last_used: str | None


def _tag_summary_system_prompt(
    entry_count: int, *, num_words: int, min_words: int, max_sentences: int
) -> str:
    return render_prompt_template(
        "tag_summary_system.txt.j2",
        entry_count=entry_count,
        num_words=num_words,
        min_words=min_words,
        max_sentences=max_sentences,
    )


def _tag_summary_response_format(min_chars: int, max_chars: int) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "tag_summary",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "minLength": min_chars,
                        "maxLength": max_chars,
                    },
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
    *,
    prior_summary: str | None = None,
    incremental: bool = False,
) -> str:
    lines = [
        f"Tag: {tag_name}",
        f"Entry count: {entry_count}",
        f"Last used: {last_used or 'unknown'}",
    ]
    if prior_summary and incremental:
        lines.extend(
            [
                "Previous summary:",
                prior_summary,
                "New snippets since last summary (most recent first):",
            ]
        )
    else:
        lines.append("Recent snippets (most recent first):")
    for sample in samples:
        lines.append(f"- {sample.created_at}: {sample.preview}")
    return "\n".join(lines)


def _clean_summary(raw: str, *, max_sentences: int, max_chars: int) -> str:
    if not raw:
        return ""
    text = " ".join(str(raw).split()).strip()
    text = text.strip('"').strip("'").strip()
    if not text:
        return ""
    sentences = [seg for seg in _SENTENCE_SPLIT.split(text) if seg]
    sentence_limit = max(1, max_sentences)
    if len(sentences) > sentence_limit:
        text = " ".join(sentences[:sentence_limit])
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip()
        if text:
            text += "..."
    return text


def _extract_summary(raw: str, *, max_sentences: int, max_chars: int) -> str:
    if not raw:
        return ""
    try:
        parsed = orjson.loads(raw)
    except Exception:
        return _clean_summary(raw, max_sentences=max_sentences, max_chars=max_chars)
    if isinstance(parsed, dict):
        summary = parsed.get("summary")
        if isinstance(summary, str):
            return _clean_summary(
                summary,
                max_sentences=max_sentences,
                max_chars=max_chars,
            )
    return _clean_summary(raw, max_sentences=max_sentences, max_chars=max_chars)


def _summary_word_count(text: str) -> int:
    return len([seg for seg in str(text).split() if seg])


def _summary_mentions_tag_once(text: str, tag_name: str) -> bool:
    if not tag_name:
        return True
    normalized = tag_name.strip()
    if not normalized:
        return True
    matches = re.findall(re.escape(normalized), text, flags=re.IGNORECASE)
    return len(matches) <= 1


def _get_cache_key(key: str | None) -> str | None:
    if not key:
        return None
    return str(key).strip() or None


def _latest_entry_id(samples: Sequence[TagEntryPreview]) -> str | None:
    if not samples:
        return None
    entry_id = str(samples[0].entry_id or "").strip()
    return entry_id or None


def _new_samples_since(
    samples: Sequence[TagEntryPreview], last_entry_id: str | None
) -> list[TagEntryPreview]:
    if not last_entry_id:
        return list(samples)
    for idx, sample in enumerate(samples):
        if sample.entry_id == last_entry_id:
            return list(samples[:idx])
    return list(samples)


async def generate_tag_summary(
    llm,
    tag_name: str,
    entry_count: int,
    last_used: str | None,
    samples: Sequence[TagEntryPreview],
    *,
    cache_key: str | None = None,
    num_words: int = 28,
) -> str:
    if not tag_name or not samples:
        return ""
    requested_words = max(18, min(int(num_words), 160))
    if requested_words <= 36:
        max_sentences = 2
        min_words = max(12, int(requested_words * 0.6))
    elif requested_words <= 60:
        max_sentences = 3
        min_words = max(24, int(requested_words * 0.7))
    else:
        max_sentences = 4
        min_words = max(40, int(requested_words * 0.8))
    max_chars = max(180, min(requested_words * 12, 1600))
    min_chars = max(80, min_words * 4)
    system_prompt = _tag_summary_system_prompt(
        entry_count,
        num_words=requested_words,
        min_words=min_words,
        max_sentences=max_sentences,
    )
    cache_key = _get_cache_key(cache_key)
    cached = _SUMMARY_CACHE.get(cache_key) if cache_key else None
    latest_entry_id = _latest_entry_id(samples)
    if cached and cached.summary:
        if (
            cached.count == entry_count
            and cached.last_entry_id == latest_entry_id
            and cached.last_used == last_used
        ):
            return cached.summary
        new_samples = _new_samples_since(samples, cached.last_entry_id)
        if new_samples and len(new_samples) < len(samples):
            user_prompt = _build_user_prompt(
                tag_name,
                entry_count,
                last_used,
                new_samples,
                prior_summary=cached.summary,
                incremental=True,
            )
        else:
            user_prompt = _build_user_prompt(tag_name, entry_count, last_used, samples)
    else:
        user_prompt = _build_user_prompt(tag_name, entry_count, last_used, samples)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    params = {
        "temperature": 0.3,
        "n_predict": max(160, requested_words * 6),
        "response_format": _tag_summary_response_format(min_chars, max_chars),
    }

    try:
        raw = await llm.complete_messages(messages, params=params)
    except Exception:
        logger.exception("Tag summary request failed")
        return ""

    summary = _extract_summary(
        raw,
        max_sentences=max_sentences,
        max_chars=max_chars,
    )
    if _summary_word_count(summary) >= min_words and _summary_mentions_tag_once(
        summary, tag_name
    ):
        if cache_key and summary:
            _SUMMARY_CACHE[cache_key] = TagSummaryState(
                summary=summary,
                last_entry_id=latest_entry_id,
                count=entry_count,
                last_used=last_used,
            )
        return summary

    retry_messages = [
        {
            "role": "system",
            "content": (
                _tag_summary_system_prompt(
                    entry_count,
                    num_words=requested_words,
                    min_words=min_words,
                    max_sentences=max_sentences,
                )
                + f"\nTarget about {requested_words} words."
                + f" Keep it within {max_sentences} sentence(s)."
                + f" Minimum {min_words} words."
                + " Use the tag name once (no repetition)."
                " Avoid single-word outputs."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw_retry = await llm.complete_messages(retry_messages, params=params)
    except Exception:
        logger.exception("Tag summary retry failed")
        if cache_key and summary:
            _SUMMARY_CACHE[cache_key] = TagSummaryState(
                summary=summary,
                last_entry_id=latest_entry_id,
                count=entry_count,
                last_used=last_used,
            )
        return summary

    retry_summary = _extract_summary(
        raw_retry,
        max_sentences=max_sentences,
        max_chars=max_chars,
    )
    final = retry_summary or summary
    if cache_key and final:
        _SUMMARY_CACHE[cache_key] = TagSummaryState(
            summary=final,
            last_entry_id=latest_entry_id,
            count=entry_count,
            last_used=last_used,
        )
    return final


__all__ = ["generate_tag_summary"]
