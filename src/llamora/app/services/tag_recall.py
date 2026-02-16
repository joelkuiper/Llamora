from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence, TypedDict

import orjson
import tiktoken

from llamora.app.services.crypto import CryptoContext
from llamora.app.services.tag_recall_cache import (
    CacheKey,
    get_tag_recall_store,
    tag_recall_namespace,
)
from llamora.app.services.summarize import SummaryPrompt
from llamora.app.services.digest_policy import (
    digest_policy_tag,
    recall_cache_digest_inputs,
)
from llamora.app.util.number import coerce_int
from llamora.llm.prompt_templates import render_prompt_template
from llamora.llm.tokenizers.tokenizer import count_message_tokens
from llamora.settings import settings


@dataclass(slots=True)
class TagRecallContext:
    """Represents summarised cross-day memories for tagged content."""

    text: str
    tags: tuple[str, ...]


class TagRecallSnippet(TypedDict):
    tag: str
    text: str


@dataclass(frozen=True, slots=True)
class RecallPlanTagSlice:
    """Recall inputs prepared for a single tag."""

    tag_hash: bytes
    tag_label: str
    snippets: tuple[tuple[str, str, str], ...]
    summary_lines: tuple[str, ...]
    digests: tuple[str, ...]
    timestamps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecallPlan:
    """Prepared per-tag recall candidates for snippet rendering."""

    slices: tuple[RecallPlanTagSlice, ...]

    def get_slice(self, tag_hash: bytes) -> RecallPlanTagSlice | None:
        for slice_ in self.slices:
            if slice_.tag_hash == tag_hash:
                return slice_
        return None


_SUMMARY_CACHE_LIMIT_FALLBACK = 512


@dataclass(frozen=True, slots=True)
class TagRecallConfig:
    """Runtime configuration for tag-based recall."""

    summary_max_chars: int
    summary_input_max_chars: int
    history_scope: int
    max_tags: int
    max_snippets: int
    snippet_max_chars: int
    mode: str
    llm_max_tokens: int
    summary_parallel: int
    background_summarize: bool
    summary_cache_max: int

    @classmethod
    def from_settings(cls) -> "TagRecallConfig":
        """Construct a TagRecallConfig from application settings."""

        def _get_int(key: str, default: int, *, minimum: int = 0) -> int:
            result = coerce_int(
                settings.get(key, default), minimum=minimum, default=default
            )
            return result if result is not None else default

        mode = (
            str(settings.get("TAG_RECALL.mode", "hybrid") or "hybrid").strip().lower()
        )
        if mode not in {"summary", "extractive", "hybrid"}:
            mode = "hybrid"

        return cls(
            summary_max_chars=_get_int("TAG_RECALL.summary_max_chars", 640),
            summary_input_max_chars=_get_int("TAG_RECALL.summary_input_max_chars", 360),
            history_scope=_get_int("TAG_RECALL.history_scope", 20),
            max_tags=_get_int("TAG_RECALL.max_tags", 5),
            max_snippets=_get_int("TAG_RECALL.max_snippets", 5),
            snippet_max_chars=_get_int("TAG_RECALL.snippet_max_chars", 160),
            mode=mode,
            llm_max_tokens=_get_int("TAG_RECALL.llm_max_tokens", 256),
            summary_parallel=_get_int("TAG_RECALL.summary_parallel", 2, minimum=1),
            background_summarize=bool(
                settings.get("TAG_RECALL.background_summarize", False)
            ),
            summary_cache_max=_get_int(
                "TAG_RECALL.summary_cache_max", _SUMMARY_CACHE_LIMIT_FALLBACK
            ),
        )


def get_tag_recall_config() -> TagRecallConfig:
    """Return the configured limits for tag-based recall."""
    return TagRecallConfig.from_settings()


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
    plan_slice: RecallPlanTagSlice,
    *,
    max_chars: int,
    input_max_chars: int,
    max_snippets: int,
) -> CacheKey:
    digest = recall_cache_digest_inputs(
        plan_slice.digests,
        max_chars=max_chars,
        input_max_chars=input_max_chars,
        max_snippets=max_snippets,
    )
    return f"recall:{digest_policy_tag()}:{digest}"


async def _summarize_with_llm(
    summarize_service,
    llm,
    text: str,
    *,
    max_chars: int,
    ctx: CryptoContext,
    tag_hash_hex: str,
    max_tokens: int,
    cache_limit: int,
    cache_key: CacheKey,
    store,
) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""

    namespace = tag_recall_namespace(tag_hash_hex)
    cached = await store.get_text(ctx, namespace, cache_key)
    if cached is not None:
        return cached

    system_prompt = _summary_system_prompt(max_chars)
    user_prompt = clean

    n_predict = max_tokens if max_tokens > 0 else 256

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        params = {
            "temperature": 0.2,
            "n_predict": n_predict,
            "response_format": _summary_response_format(),
        }
        prompt_tokens = sum(
            count_message_tokens(msg["role"], msg["content"]) for msg in messages
        )
        max_prompt = llm.prompt_budget.max_prompt_tokens(params)
        if max_prompt is not None and prompt_tokens > max_prompt:
            system_tokens = count_message_tokens("system", system_prompt)
            budget_for_user = max(max_prompt - system_tokens, 0)
            if budget_for_user > 0:
                user_prompt = _truncate_to_tokens(user_prompt, budget_for_user)
            else:
                user_prompt = ""
    except Exception:  # pragma: no cover - defensive
        pass

    if not user_prompt:
        return ""

    prompt = SummaryPrompt(
        system=system_prompt,
        user=user_prompt,
        temperature=0.2,
        max_tokens=n_predict,
        response_format=_summary_response_format(),
    )

    try:
        summary = await summarize_service.generate(prompt)
    except Exception:
        return ""

    if not summary:
        retry_prompt = SummaryPrompt(
            system=system_prompt
            + '\nReturn ONLY strict JSON matching {"summary":"..."}.',
            user=user_prompt,
            temperature=0.0,
            max_tokens=n_predict,
            response_format=_summary_response_format(),
        )
        try:
            summary = await summarize_service.generate(retry_prompt)
        except Exception:
            return ""
        if not summary:
            return ""
    summary = _truncate_text(summary, max_chars)
    if summary and cache_limit > 0:
        await store.set_text(ctx, namespace, cache_key, summary)
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


def _build_extractive_snippet(
    *,
    items: list[tuple[str, str, str]],
    max_chars: int,
) -> str:
    if not items:
        return ""
    pieces: list[str] = []
    for when, role, text in items:
        cleaned = _truncate_text(text, max_chars)
        if not cleaned:
            continue
        pieces.append(f"{when}: {cleaned}")
    if not pieces:
        return ""
    joined = " · ".join(pieces)
    return joined.strip()


async def _build_recall_plan(
    db,
    ctx: CryptoContext,
    *,
    focus_tags: Sequence[bytes],
    tag_names: Mapping[bytes, str],
    history_ids: set[str],
    current_date: str | None,
    max_snippets: int,
    summary_input_max_chars: int,
    max_entry_id: str | None,
    max_created_at: str | None,
) -> RecallPlan:
    tag_entries = await db.tags.get_recent_entries_by_tag_hashes(
        ctx.user_id,
        focus_tags,
        per_tag_limit=max_snippets,
        max_entry_id=max_entry_id,
        max_created_at=max_created_at,
    )
    all_ids: list[str] = []
    seen_ids: set[str] = set()
    for tag_hash in focus_tags:
        for entry_id in tag_entries.get(tag_hash, []):
            if entry_id not in seen_ids:
                seen_ids.add(entry_id)
                all_ids.append(entry_id)

    if not all_ids:
        return RecallPlan(slices=tuple())

    candidate_entries = await db.entries.get_recall_candidates_by_ids(
        ctx,
        all_ids,
        max_entry_id=max_entry_id,
        max_created_at=max_created_at,
    )
    by_id = {str(item.get("id")): item for item in candidate_entries if item.get("id")}

    slices: list[RecallPlanTagSlice] = []
    for tag_hash in focus_tags:
        ids_for_tag = tag_entries.get(tag_hash, [])
        if not ids_for_tag:
            continue

        records: list[tuple[str, str, str, str, str]] = []
        for entry_id in ids_for_tag:
            if len(records) >= max_snippets:
                break
            entry = by_id.get(str(entry_id))
            if not entry:
                continue
            if str(entry_id) in history_ids:
                continue
            created_at = entry.get("created_at")
            created_date = _extract_date(created_at)
            if current_date and created_date == current_date:
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            if summary_input_max_chars > 0:
                text = _truncate_text(text, summary_input_max_chars)
                if not text:
                    continue
            when = created_date or "Previous day"
            role = str(entry.get("role") or "assistant").strip().title()
            digest = str(entry.get("digest") or "").strip() or f"missing:{entry_id}"
            stamp = str(created_at or "")
            records.append((when, role, text, digest, stamp))

        if not records:
            continue

        focus_records = [r for r in records if r[1].lower() == "user"] or records
        tag_label = tag_names.get(tag_hash, "") or tag_hash.hex()[:8]
        snippets = tuple(
            (when, role, text) for when, role, text, _digest, _stamp in focus_records
        )
        summary_lines = tuple(
            f"{when} {role}: {text}"
            for when, role, text, _digest, _stamp in focus_records
        )
        digests = tuple(digest for _when, _role, _text, digest, _stamp in focus_records)
        timestamps = tuple(
            stamp for _when, _role, _text, _digest, stamp in focus_records
        )
        slices.append(
            RecallPlanTagSlice(
                tag_hash=tag_hash,
                tag_label=tag_label,
                snippets=snippets,
                summary_lines=summary_lines,
                digests=digests,
                timestamps=timestamps,
            )
        )

    return RecallPlan(slices=tuple(slices))


async def build_tag_recall_context(
    db,
    ctx: CryptoContext,
    *,
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    current_date: str | None,
    llm,
    summarize_service,
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
        or (cfg.mode == "summary" and cfg.summary_max_chars <= 0)
    ):
        return None
    if cfg.mode == "extractive" and cfg.snippet_max_chars <= 0:
        return None
    if cfg.summary_input_max_chars < 0:
        return None

    if target_entry_id:
        entry_tags = await db.tags.get_tags_for_entry(ctx, target_entry_id)
        focus_tags, tag_names = _extract_raw_tags(entry_tags, cfg.max_tags)
    else:
        focus_tags, tag_names = _extract_focus_tags(
            history,
            limit=cfg.max_tags,
            lookback=cfg.history_scope,
        )
    if not focus_tags:
        return None

    history_ids: set[str] = set()
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        entry_id = entry.get("id")
        if entry_id:
            history_ids.add(str(entry_id))

    focus_slice = focus_tags[: cfg.max_tags]
    plan = await _build_recall_plan(
        db,
        ctx,
        focus_tags=focus_slice,
        tag_names=tag_names,
        history_ids=history_ids,
        current_date=current_date,
        max_snippets=cfg.max_snippets,
        summary_input_max_chars=cfg.summary_input_max_chars,
        max_entry_id=max_entry_id,
        max_created_at=max_created_at,
    )
    if not plan.slices:
        return None

    store = get_tag_recall_store(db)

    async def _build_tag_snippet(tag_digest: bytes) -> TagRecallSnippet | None:
        plan_slice = plan.get_slice(tag_digest)
        if plan_slice is None or not plan_slice.summary_lines:
            return None

        tag_hash = tag_digest.hex()
        aggregate = "\n".join(plan_slice.summary_lines)

        cache_key = _build_summary_cache_key(
            plan_slice,
            max_chars=cfg.summary_max_chars,
            input_max_chars=cfg.summary_input_max_chars,
            max_snippets=cfg.max_snippets,
        )
        namespace = tag_recall_namespace(tag_hash)
        cached = await store.get_text(ctx, namespace, cache_key)

        if cfg.mode == "summary":
            if llm is None:
                return None
            summary = await _summarize_with_llm(
                summarize_service,
                llm,
                aggregate,
                max_chars=cfg.summary_max_chars,
                ctx=ctx,
                tag_hash_hex=tag_hash,
                max_tokens=cfg.llm_max_tokens,
                cache_limit=cfg.summary_cache_max,
                cache_key=cache_key,
                store=store,
            )
            if not summary:
                return None
            return {"tag": plan_slice.tag_label, "text": summary}

        if cfg.mode == "hybrid" and cached:
            return {"tag": plan_slice.tag_label, "text": cached}

        snippet = _build_extractive_snippet(
            items=list(plan_slice.snippets),
            max_chars=cfg.snippet_max_chars,
        )
        if not snippet:
            return None

        if cfg.mode == "hybrid" and cfg.background_summarize and llm is not None:
            bg_ctx = ctx.fork()

            async def _background() -> None:
                try:
                    await _summarize_with_llm(
                        summarize_service,
                        llm,
                        aggregate,
                        max_chars=cfg.summary_max_chars,
                        ctx=bg_ctx,
                        tag_hash_hex=tag_hash,
                        max_tokens=cfg.llm_max_tokens,
                        cache_limit=cfg.summary_cache_max,
                        cache_key=cache_key,
                        store=store,
                    )
                finally:
                    bg_ctx.drop()

            asyncio.create_task(_background())

        return {"tag": plan_slice.tag_label, "text": snippet}

    async def _run_tag_snippet(
        tag_digest: bytes, sem: asyncio.Semaphore
    ) -> TagRecallSnippet | None:
        async with sem:
            return await _build_tag_snippet(tag_digest)

    sem = asyncio.Semaphore(min(cfg.summary_parallel, max(len(focus_slice), 1)))
    tasks = [_run_tag_snippet(tag_digest, sem) for tag_digest in focus_slice]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    snippets = [
        result
        for result in results
        if isinstance(result, Mapping)
        and str(result.get("text") or "").strip()
        and str(result.get("tag") or "").strip()
    ]

    if not snippets:
        return None

    tag_labels = [str(snippet["tag"]) for snippet in snippets]
    text = render_prompt_template(
        "tag_recall.txt.j2",
        heading="Context for the active entry",
        tag_labels=tag_labels,
        snippets=snippets,
    ).strip()
    if not text:
        return None

    return TagRecallContext(text=text, tags=tuple(tag_labels))
