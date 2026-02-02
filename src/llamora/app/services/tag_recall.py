from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from sumy.nlp.tokenizers import Tokenizer
from sumy.parsers.plaintext import PlaintextParser
from sumy.summarizers.lex_rank import LexRankSummarizer

from llamora.llm.prompt_templates import render_prompt_template
from llamora.settings import settings


@dataclass(slots=True)
class TagRecallContext:
    """Represents summarised cross-day memories for tagged content."""

    text: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TagRecallConfig:
    """Runtime configuration for tag-based recall."""

    summary_sentences: int
    summary_max_chars: int
    history_scope: int
    max_tags: int
    max_snippets: int


def _coerce_int(value: Any, default: int, *, minimum: int | None = None) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and candidate < minimum:
        return default
    return candidate


def get_tag_recall_config() -> TagRecallConfig:
    """Return the configured limits for tag-based recall."""

    summary_sentences = _coerce_int(
        settings.get("TAG_RECALL.summary_sentences", 4),
        4,
        minimum=1,
    )
    summary_max_chars = _coerce_int(
        settings.get("TAG_RECALL.summary_max_chars", 640),
        640,
        minimum=0,
    )
    history_scope = _coerce_int(
        settings.get("TAG_RECALL.history_scope", 20),
        20,
        minimum=0,
    )
    max_tags = _coerce_int(
        settings.get("TAG_RECALL.max_tags", 5),
        5,
        minimum=0,
    )
    max_snippets = _coerce_int(
        settings.get("TAG_RECALL.max_snippets", 5),
        5,
        minimum=0,
    )

    return TagRecallConfig(
        summary_sentences=summary_sentences,
        summary_max_chars=summary_max_chars,
        history_scope=history_scope,
        max_tags=max_tags,
        max_snippets=max_snippets,
    )


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    cutoff = max(1, max_chars - 1)
    trimmed = text[:cutoff].rstrip()
    if not trimmed:
        return "…"
    return f"{trimmed}…"


def _summarize(text: str, *, sentences: int, max_chars: int) -> str:
    """Return a compact summary of ``text`` using LexRank."""

    clean = (text or "").strip()
    if not clean:
        return ""

    parser = PlaintextParser.from_string(clean, Tokenizer("english"))
    summarizer = LexRankSummarizer()
    summary = summarizer(parser.document, sentences)

    parts = [str(sentence).strip() for sentence in summary if str(sentence).strip()]
    if parts:
        joined = " ".join(parts)
        return _truncate_text(joined, max_chars)

    return _truncate_text(clean, max_chars)


def _extract_focus_tags(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    limit: int,
    lookback: int,
) -> tuple[list[bytes], dict[bytes, str]]:
    """Return the most recent unique tag hashes present in ``history``."""

    tag_hashes: list[bytes] = []
    tag_names: dict[bytes, str] = {}
    seen: set[bytes] = set()

    history_list = list(history)
    recent_history = history_list[-lookback:] if lookback > 0 else history_list
    for entry in reversed(recent_history):
        tags = entry.get("tags") if isinstance(entry, Mapping) else None
        if not isinstance(tags, Iterable):
            continue
        for raw_tag in tags:
            if not isinstance(raw_tag, Mapping):
                continue
            hash_hex = str(raw_tag.get("hash") or "").strip()
            if not hash_hex:
                continue
            try:
                digest = bytes.fromhex(hash_hex)
            except ValueError:
                continue
            if digest in seen:
                continue
            seen.add(digest)
            tag_hashes.append(digest)
            name = str(raw_tag.get("name") or "").strip()
            if name:
                tag_names.setdefault(digest, name)
            if len(tag_hashes) >= limit:
                return tag_hashes, tag_names

    return tag_hashes, tag_names


def _extract_date(created_at: str | None) -> str | None:
    if not created_at:
        return None
    try:
        return datetime.fromisoformat(created_at).date().isoformat()
    except ValueError:
        return created_at[:10]


async def build_tag_recall_context(
    db,
    user_id: str,
    dek: bytes,
    *,
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    current_date: str | None,
    max_entry_id: str | None = None,
    max_created_at: str | None = None,
    config: TagRecallConfig | None = None,
) -> TagRecallContext | None:
    """Return summarised memories linked to the user's recent tags.

    Cutoffs prevent recalling entries created after the triggering entry.
    """

    if not history:
        return None

    cfg = config or get_tag_recall_config()

    if (
        cfg.max_tags <= 0
        or cfg.max_snippets <= 0
        or cfg.summary_max_chars <= 0
        or cfg.summary_sentences <= 0
    ):
        return None

    focus_tags, tag_names = _extract_focus_tags(
        history,
        limit=cfg.max_tags,
        lookback=cfg.history_scope,
    )
    if not focus_tags:
        return None

    candidate_ids = await db.tags.get_recent_entries_for_tag_hashes(
        user_id,
        focus_tags,
        limit=cfg.max_snippets * 4,
        max_entry_id=max_entry_id,
        max_created_at=max_created_at,
    )

    if not candidate_ids:
        return None

    history_ids: set[str] = set()
    history_map: dict[str, Mapping[str, Any]] = {}
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        entry_id = entry.get("id")
        if not entry_id:
            continue
        key = str(entry_id)
        history_ids.add(key)
        history_map[key] = entry

    candidate_entries = await db.entries.get_entries_by_ids(
        user_id, candidate_ids, dek
    )

    if not candidate_entries:
        return None

    by_id = {str(item.get("id")): item for item in candidate_entries if item.get("id")}

    reply_to_ids: list[str] = []
    for entry in candidate_entries:
        reply_to = entry.get("reply_to")
        if not reply_to:
            continue
        reply_key = str(reply_to)
        if reply_key in history_map:
            continue
        if reply_key not in by_id and reply_key not in reply_to_ids:
            reply_to_ids.append(reply_key)

    reply_entries: list[Mapping[str, Any]] = []
    if reply_to_ids:
        reply_entries = await db.entries.get_entries_by_ids(
            user_id, reply_to_ids, dek
        )

    reply_lookup: dict[str, Mapping[str, Any]] = {
        **{str(item.get("id")): item for item in reply_entries if item.get("id")},
        **history_map,
    }

    snippets: list[str] = []
    per_summary_max_chars = max(cfg.summary_max_chars // 2, 1)
    for msg_id in candidate_ids:
        if len(snippets) >= cfg.max_snippets:
            break
        entry = by_id.get(str(msg_id))
        if not entry:
            continue
        if str(msg_id) in history_ids:
            continue
        created_at = entry.get("created_at")
        if current_date and _extract_date(created_at) == current_date:
            continue
        text = str(entry.get("message") or "").strip()
        if not text:
            continue
        summary_assistant = _summarize(
            text,
            sentences=cfg.summary_sentences,
            max_chars=per_summary_max_chars,
        )
        if not summary_assistant:
            continue
        reply_summary = ""
        reply_to = entry.get("reply_to")
        if reply_to:
            reply_entry = reply_lookup.get(str(reply_to))
            if isinstance(reply_entry, Mapping):
                reply_text = str(reply_entry.get("message") or "").strip()
                if reply_text:
                    reply_summary = _summarize(
                        reply_text,
                        sentences=cfg.summary_sentences,
                        max_chars=per_summary_max_chars,
                    )
        parts: list[str] = []
        if reply_summary:
            parts.append(f"User: {reply_summary}")
        parts.append(f"Assistant: {summary_assistant}")
        combined = _truncate_text(" ".join(parts), cfg.summary_max_chars)
        if not combined:
            continue
        when = _extract_date(created_at) or "Previous day"
        snippets.append(f"{when}: {combined}")

    if not snippets:
        return None

    tag_labels = [
        tag_names.get(tag, "") for tag in focus_tags if tag_names.get(tag, "")
    ]
    text = render_prompt_template(
        "tag_recall.txt.j2",
        heading="Cross-day tag recall",
        tag_labels=tag_labels,
        snippets=snippets,
    ).strip()
    if not text:
        return None

    return TagRecallContext(text=text, tags=tuple(tag_labels))
