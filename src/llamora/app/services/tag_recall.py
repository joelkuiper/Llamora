from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence, cast

import orjson
import tiktoken

from llamora.llm.prompt_templates import render_prompt_template
from llamora.llm.tokenizers.tokenizer import count_message_tokens
from llamora.settings import settings
from llamora.app.util.number import coerce_int
from llamora.app.db.events import ENTRY_TAGS_CHANGED_EVENT, RepositoryEventBus


@dataclass(slots=True)
class TagRecallContext:
    """Represents summarised cross-day memories for tagged content."""

    text: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TagRecallConfig:
    """Runtime configuration for tag-based recall."""

    summary_max_chars: int
    history_scope: int
    max_tags: int
    max_snippets: int
    summary_cache_max: int


CacheKey = tuple[str, str, str, int]

_SUMMARY_CACHE_LIMIT_FALLBACK = 512


def get_tag_recall_config() -> TagRecallConfig:
    """Return the configured limits for tag-based recall."""

    summary_max_chars = cast(
        int,
        coerce_int(
            settings.get("TAG_RECALL.summary_max_chars", 640),
            minimum=0,
            default=640,
        ),
    )
    history_scope = cast(
        int,
        coerce_int(
            settings.get("TAG_RECALL.history_scope", 20),
            minimum=0,
            default=20,
        ),
    )
    max_tags = cast(
        int,
        coerce_int(
            settings.get("TAG_RECALL.max_tags", 5),
            minimum=0,
            default=5,
        ),
    )
    max_snippets = cast(
        int,
        coerce_int(
            settings.get("TAG_RECALL.max_snippets", 5),
            minimum=0,
            default=5,
        ),
    )
    summary_cache_max = cast(
        int,
        coerce_int(
            settings.get("TAG_RECALL.summary_cache_max", _SUMMARY_CACHE_LIMIT_FALLBACK),
            minimum=0,
            default=_SUMMARY_CACHE_LIMIT_FALLBACK,
        ),
    )

    return TagRecallConfig(
        summary_max_chars=summary_max_chars,
        history_scope=history_scope,
        max_tags=max_tags,
        max_snippets=max_snippets,
        summary_cache_max=summary_cache_max,
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


def _summary_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "tag_recall_summary",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    }


def _summary_system_prompt(max_chars: int) -> str:
    return render_prompt_template(
        "tag_recall_summary_system.txt.j2",
        max_chars=max_chars,
    )


def _extract_summary_payload(raw: str) -> str:
    if not raw:
        return ""
    try:
        parsed = orjson.loads(raw)
    except Exception:
        return ""
    if isinstance(parsed, dict):
        summary = parsed.get("summary")
        if isinstance(summary, str):
            return summary.strip()
    return ""


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    clean = (text or "").strip()
    if not clean:
        return ""
    encoding_name = settings.get("LLM.tokenizer.encoding", "cl100k_base")
    encoding = tiktoken.get_encoding(str(encoding_name))
    encoded = encoding.encode(clean)
    if len(encoded) <= max_tokens:
        return clean
    trimmed = encoding.decode(encoded[:max_tokens]).strip()
    return trimmed


def _build_summary_cache_key(
    user_id: str,
    tag_hash_hex: str,
    text: str,
    max_chars: int,
) -> CacheKey:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (user_id, tag_hash_hex, digest, max_chars)


class TagRecallSummaryCache:
    """Cache LLM-generated summaries keyed by tag hash."""

    __slots__ = ("_entries", "_by_tag", "_order")

    def __init__(self) -> None:
        self._entries: dict[CacheKey, str] = {}
        self._by_tag: dict[tuple[str, str], set[CacheKey]] = {}
        self._order: deque[CacheKey] = deque()

    def get(self, key: CacheKey) -> str | None:
        return self._entries.get(key)

    def set(self, key: CacheKey, summary: str, *, max_entries: int) -> None:
        if max_entries <= 0:
            return
        if key in self._entries:
            self._entries[key] = summary
            return
        self._entries[key] = summary
        user_id, tag_hash_hex, *_ = key
        self._by_tag.setdefault((user_id, tag_hash_hex), set()).add(key)
        self._order.append(key)
        self._evict_overflow(max_entries)

    def invalidate_tag(self, user_id: str, tag_hash_hex: str) -> None:
        tag_key = (user_id, tag_hash_hex)
        keys = self._by_tag.pop(tag_key, set())
        for key in keys:
            self._delete_key(key)

    def _delete_key(self, key: CacheKey) -> None:
        if key not in self._entries:
            return
        self._entries.pop(key, None)
        user_id, tag_hash_hex, *_ = key
        tag_key = (user_id, tag_hash_hex)
        tag_keys = self._by_tag.get(tag_key)
        if tag_keys:
            tag_keys.discard(key)
            if not tag_keys:
                self._by_tag.pop(tag_key, None)

    def _evict_overflow(self, max_entries: int) -> None:
        if max_entries <= 0:
            return
        while len(self._entries) > max_entries and self._order:
            key = self._order.popleft()
            if key in self._entries:
                self._delete_key(key)


TAG_RECALL_SUMMARY_CACHE = TagRecallSummaryCache()


class TagRecallCacheSynchronizer:
    """Invalidate tag recall summaries when tag assignments change."""

    __slots__ = ("_cache", "_events", "_entries")

    def __init__(
        self,
        *,
        event_bus: RepositoryEventBus | None,
        entries_repository,
        cache: TagRecallSummaryCache,
    ) -> None:
        self._cache = cache
        self._events = event_bus
        self._entries = entries_repository
        if not self._events:
            return
        self._events.subscribe(ENTRY_TAGS_CHANGED_EVENT, self._handle_tags_changed)

    async def _handle_tags_changed(
        self,
        *,
        user_id: str,
        entry_id: str,
        tag_hash: bytes | str | None = None,
        created_date: str | None = None,
        client_today: str | None = None,
    ) -> None:
        if not tag_hash or not self._entries:
            return
        entry_date = created_date
        if not entry_date:
            entry_date = await self._entries.get_entry_date(user_id, entry_id)
        if not entry_date:
            return
        today_iso = client_today
        if not today_iso:
            today_iso = datetime.now(timezone.utc).date().isoformat()
        if entry_date == today_iso:
            return
        tag_hash_hex = tag_hash.hex() if isinstance(tag_hash, bytes) else str(tag_hash)
        self._cache.invalidate_tag(user_id, tag_hash_hex)


async def _summarize_with_llm(
    llm,
    text: str,
    *,
    max_chars: int,
    user_id: str,
    tag_hash_hex: str,
    cache_limit: int,
) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""

    cache_key = _build_summary_cache_key(user_id, tag_hash_hex, clean, max_chars)
    cached = TAG_RECALL_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    system_prompt = _summary_system_prompt(max_chars)
    user_prompt = clean
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    params = {
        "temperature": 0.2,
        "n_predict": 512,
        "response_format": _summary_response_format(),
    }

    try:
        prompt_tokens = sum(
            count_message_tokens(msg["role"], msg["content"]) for msg in messages
        )
        max_prompt = llm.prompt_budget.max_prompt_tokens(params)
        if max_prompt is not None and prompt_tokens > max_prompt:
            # Trim user content to fit within the available prompt budget.
            system_tokens = count_message_tokens("system", system_prompt)
            budget_for_user = max(max_prompt - system_tokens, 0)
            if budget_for_user > 0:
                user_prompt = _truncate_to_tokens(user_prompt, budget_for_user)
                messages[1]["content"] = user_prompt
            else:
                user_prompt = ""
                messages[1]["content"] = ""
    except Exception:  # pragma: no cover - defensive
        pass

    if not user_prompt:
        return ""

    try:
        raw = await llm.complete_messages(messages, params=params)
    except Exception:
        return ""

    summary = _extract_summary_payload(raw)
    if not summary:
        retry_messages = [
            {
                "role": "system",
                "content": system_prompt
                + '\nReturn ONLY strict JSON matching {"summary":"..."}.',
            },
            {"role": "user", "content": user_prompt},
        ]
        try:
            raw_retry = await llm.complete_messages(
                retry_messages,
                params={
                    **params,
                    "temperature": 0.0,
                },
            )
        except Exception:
            return ""
        summary = _extract_summary_payload(raw_retry)
        if not summary:
            return ""
    summary = _truncate_text(summary, max_chars)
    if summary:
        TAG_RECALL_SUMMARY_CACHE.set(cache_key, summary, max_entries=cache_limit)
    return summary


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


def _extract_raw_tags(
    tags: Sequence[Mapping[str, Any] | dict[str, Any]],
    limit: int,
) -> tuple[list[bytes], dict[bytes, str]]:
    tag_hashes: list[bytes] = []
    tag_names: dict[bytes, str] = {}
    seen: set[bytes] = set()

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
            break
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
    llm,
    max_entry_id: str | None = None,
    max_created_at: str | None = None,
    config: TagRecallConfig | None = None,
    target_entry_id: str | None = None,
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
        or llm is None
    ):
        return None

    if target_entry_id:
        entry_tags = await db.tags.get_tags_for_entry(user_id, target_entry_id, dek)
        focus_tags, tag_names = _extract_raw_tags(entry_tags, cfg.max_tags)
    else:
        focus_tags, tag_names = _extract_focus_tags(
            history,
            limit=cfg.max_tags,
            lookback=cfg.history_scope,
        )
    if not focus_tags:
        return None

    tag_entry_ids: dict[bytes, list[str]] = {}
    all_ids: list[str] = []
    seen_ids: set[str] = set()
    for tag_hash in focus_tags:
        ids = await db.tags.get_recent_entries_for_tag_hashes(
            user_id,
            [tag_hash],
            limit=cfg.max_snippets,
            max_entry_id=max_entry_id,
            max_created_at=max_created_at,
        )
        if not ids:
            continue
        tag_entry_ids[tag_hash] = ids
        for entry_id in ids:
            if entry_id not in seen_ids:
                seen_ids.add(entry_id)
                all_ids.append(entry_id)

    if not all_ids:
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

    candidate_entries = await db.entries.get_entries_by_ids(user_id, all_ids, dek)

    if not candidate_entries:
        return None

    by_id = {str(item.get("id")): item for item in candidate_entries if item.get("id")}

    # Aggregate by tag; replies are handled implicitly via tagged entries.

    async def _build_tag_snippet(tag_digest: bytes) -> str | None:
        ids_for_tag = tag_entry_ids.get(tag_digest, [])
        if not ids_for_tag:
            return None
        tag_hash = tag_digest.hex()
        tag_items: list[tuple[str, str, str]] = []
        for entry_id in ids_for_tag:
            if len(tag_items) >= cfg.max_snippets:
                break
            entry = by_id.get(str(entry_id))
            if not entry:
                continue
            if str(entry_id) in history_ids:
                continue
            created_at = entry.get("created_at")
            if current_date and _extract_date(created_at) == current_date:
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            when = _extract_date(created_at) or "Previous day"
            role = str(entry.get("role") or "assistant").strip().title()
            tag_items.append((when, role, text))

        if not tag_items:
            return None

        lines = [f"{when} {role}: {text}" for when, role, text in tag_items]
        aggregate = "\n".join(lines)
        summary = await _summarize_with_llm(
            llm,
            aggregate,
            max_chars=cfg.summary_max_chars,
            user_id=user_id,
            tag_hash_hex=tag_hash,
            cache_limit=cfg.summary_cache_max,
        )
        if not summary:
            return None
        tag_label = tag_names.get(tag_digest, "") or tag_hash[:8]
        return f"{tag_label}: {summary}"

    async def _run_tag_snippet(tag_digest: bytes, sem: asyncio.Semaphore) -> str | None:
        async with sem:
            return await _build_tag_snippet(tag_digest)

    focus_slice = focus_tags[: cfg.max_tags]
    sem = asyncio.Semaphore(min(3, max(len(focus_slice), 1)))
    tasks = [_run_tag_snippet(tag_digest, sem) for tag_digest in focus_slice]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    snippets = [
        result for result in results if isinstance(result, str) and result.strip()
    ]

    if not snippets:
        return None

    tag_labels = [
        tag_names.get(tag, "") for tag in focus_tags if tag_names.get(tag, "")
    ]
    text = render_prompt_template(
        "tag_recall.txt.j2",
        heading="Context for the active entry",
        tag_labels=tag_labels,
        snippets=snippets,
    ).strip()
    if not text:
        return None

    return TagRecallContext(text=text, tags=tuple(tag_labels))
