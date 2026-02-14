"""Service helpers for working with user tags and suggestions."""

from __future__ import annotations

from collections import Counter
import hashlib
from datetime import datetime
import logging
import re
import time
import textwrap
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from llamora.app.util.tags import canonicalize as _canonicalize, display as _display
from llamora.app.services.markdown import render_markdown_to_html
from llamora.persistence.local_db import LocalDB
from llamora.app.services.entry_metadata import generate_metadata


logger = logging.getLogger(__name__)


_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_MARKDOWN_PREFIX = re.compile(r"^(?:#{1,6}\s+|>+\s+|[-*+]\s+|\d+\.\s+)")
TagsSortKind = Literal["alpha", "count"]
TagsSortDirection = Literal["asc", "desc"]


def _parse_tag_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if not cursor:
        return None, None
    if "|" not in cursor:
        return None, None
    created_at, _, entry_id = cursor.partition("|")
    created_at = created_at.strip()
    entry_id = entry_id.strip()
    if not created_at or not entry_id:
        return None, None
    return created_at, entry_id


def _build_summary_digest(entry_count: int, last_updated: str | None) -> str:
    payload = f"{entry_count}:{last_updated or ''}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(slots=True)
class TagEntryPreview:
    entry_id: str
    created_at: str
    created_date: str | None
    preview: str


@dataclass(slots=True)
class TagOverview:
    name: str
    hash: str
    count: int
    last_used: str | None
    last_updated: str | None
    summary_digest: str
    entries: tuple[TagEntryPreview, ...]
    has_more: bool = False
    next_cursor: str | None = None


@dataclass(slots=True)
class TagIndexItem:
    name: str
    hash: str
    count: int


@dataclass(slots=True)
class TagRelatedItem:
    name: str
    count: int


@dataclass(slots=True)
class TagArchiveEntry:
    entry: dict[str, Any]
    created_date: str | None
    related_tags: tuple[str, ...]
    responses: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class TagArchiveResponse:
    entry: dict[str, Any]


@dataclass(slots=True)
class TagArchiveDetail:
    name: str
    hash: str
    count: int
    first_used: str | None
    first_used_label: str | None
    last_updated: str | None
    summary_digest: str
    entries: tuple[TagArchiveEntry, ...]
    related_tags: tuple[TagRelatedItem, ...]
    entries_has_more: bool = False
    entries_next_cursor: str | None = None


@dataclass(slots=True)
class TagsViewData:
    tags: tuple[TagIndexItem, ...]
    selected_tag: str | None
    detail: TagArchiveDetail | None
    sort_kind: TagsSortKind
    sort_dir: TagsSortDirection


class TagService:
    """Provide higher level helpers for tag canonicalisation and hydration."""

    def __init__(self, db: LocalDB) -> None:
        self._db = db
        self._suggestion_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
        self._suggestion_ttl = 300.0

    def canonicalize(self, raw: str) -> str:
        """Return the canonical representation for ``raw``."""

        return _canonicalize(raw)

    def display(self, canonical: str) -> str:
        """Return the display-ready form for ``canonical``."""

        return _display(canonical)

    def normalize_tag_query(self, raw: str | None) -> str | None:
        """Return canonical tag query value, or ``None`` when invalid/empty."""

        value = str(raw or "").strip()
        if not value:
            return None
        try:
            return self.canonicalize(value)
        except ValueError:
            return None

    def normalize_tags_sort_kind(self, raw: str | None) -> TagsSortKind:
        """Return the sort kind for the tags index."""

        value = str(raw or "").strip().lower()
        if not value:
            return "count"
        if value == "count":
            return "count"
        return "alpha"

    def normalize_tags_sort_dir(self, raw: str | None) -> TagsSortDirection:
        """Return the sort direction for the tags index."""

        value = str(raw or "").strip().lower()
        if not value:
            return "desc"
        if value == "desc":
            return "desc"
        return "asc"

    def normalize_legacy_sort(
        self,
        raw: str | None,
    ) -> tuple[TagsSortKind, TagsSortDirection] | None:
        """Translate legacy sort values into the current split model."""

        value = str(raw or "").strip().lower()
        if value == "count_desc":
            return ("count", "desc")
        if value == "count_asc":
            return ("count", "asc")
        if value == "alpha_desc":
            return ("alpha", "desc")
        if value == "alpha":
            return ("alpha", "asc")
        return None

    async def get_tag_overview(
        self,
        user_id: str,
        dek: bytes,
        tag_hash: bytes,
        *,
        limit: int = 24,
        cursor: str | None = None,
    ) -> TagOverview | None:
        """Return tag metadata and recent entry previews."""

        info = await self._db.tags.get_tag_info(user_id, tag_hash, dek)
        if not info:
            return None

        previews, next_cursor, has_more = await self.get_tag_entries_page(
            user_id,
            dek,
            tag_hash,
            limit=limit,
            cursor=cursor,
        )

        return TagOverview(
            name=self.display(info["name"]),
            hash=info["hash"],
            count=int(info.get("count", 0) or 0),
            last_used=info.get("last_used"),
            last_updated=info.get("last_updated"),
            summary_digest=_build_summary_digest(
                int(info.get("count", 0) or 0), info.get("last_updated")
            ),
            entries=tuple(previews),
            has_more=has_more,
            next_cursor=next_cursor,
        )

    async def get_tag_entries_page(
        self,
        user_id: str,
        dek: bytes,
        tag_hash: bytes,
        *,
        limit: int = 24,
        cursor: str | None = None,
    ) -> tuple[list[TagEntryPreview], str | None, bool]:
        before_created_at, before_entry_id = _parse_tag_cursor(cursor)
        (
            entry_ids,
            next_cursor,
            has_more,
        ) = await self._db.tags.get_recent_entries_page_for_tag_hashes(
            user_id,
            [tag_hash],
            limit=limit,
            before_created_at=before_created_at,
            before_entry_id=before_entry_id,
        )
        if not entry_ids:
            return [], None, False

        entries = await self._db.entries.get_entries_by_ids(user_id, entry_ids, dek)
        entry_map = {entry.get("id"): entry for entry in entries}

        previews: list[TagEntryPreview] = []
        for entry_id in entry_ids:
            entry = entry_map.get(entry_id)
            if not entry:
                continue
            created_at = str(entry.get("created_at") or "").strip()
            if not created_at:
                continue
            preview = _extract_preview_line(entry.get("text", ""))
            if not preview:
                preview = "..."
            previews.append(
                TagEntryPreview(
                    entry_id=entry_id,
                    created_at=created_at,
                    created_date=entry.get("created_date"),
                    preview=preview,
                )
            )

        return previews, next_cursor, has_more

    async def get_tags_view_data(
        self,
        user_id: str,
        dek: bytes,
        selected_tag: str | None,
        *,
        sort_kind: TagsSortKind = "alpha",
        sort_dir: TagsSortDirection = "asc",
        entry_limit: int = 12,
        around_entry_id: str | None = None,
        secondary_tag_limit: int = 4,
        related_tag_limit: int = 2,
    ) -> TagsViewData:
        """Return data for the two-column archival tags view."""
        raw_tags = await self._db.tags.get_tags_index(user_id, dek)
        index_items: list[TagIndexItem] = []
        for row in raw_tags:
            raw_name = str(row.get("name") or "").strip()
            if not raw_name:
                continue
            try:
                canonical = self.canonicalize(raw_name)
            except ValueError:
                continue
            index_items.append(
                TagIndexItem(
                    name=self.display(canonical),
                    hash=str(row.get("hash") or ""),
                    count=int(row.get("count") or 0),
                )
            )
        index_items = self._sort_index_items(
            index_items,
            sort_kind=sort_kind,
            sort_dir=sort_dir,
        )
        selected_name = self.normalize_tag_query(selected_tag)
        selected_index = (
            next((item for item in index_items if item.name == selected_name), None)
            if selected_name
            else None
        )
        if selected_name and not selected_index:
            selected_index = await self._resolve_index_item_by_name(
                user_id, dek, selected_name
            )
            if selected_index and not any(
                item.name == selected_index.name for item in index_items
            ):
                index_items = [selected_index, *index_items]
        if not selected_index and index_items:
            selected_index = index_items[0]
        if not selected_index:
            return TagsViewData(
                tags=tuple(index_items),
                selected_tag=None,
                detail=None,
                sort_kind=sort_kind,
                sort_dir=sort_dir,
            )

        detail = await self._build_archive_detail(
            user_id,
            dek,
            selected_index,
            limit=entry_limit,
            around_entry_id=around_entry_id,
            secondary_tag_limit=secondary_tag_limit,
            related_tag_limit=related_tag_limit,
        )
        return TagsViewData(
            tags=tuple(index_items),
            selected_tag=selected_index.name if detail else None,
            detail=detail,
            sort_kind=sort_kind,
            sort_dir=sort_dir,
        )

    async def _resolve_index_item_by_name(
        self,
        user_id: str,
        dek: bytes,
        tag_name: str,
    ) -> TagIndexItem | None:
        target = self.normalize_tag_query(tag_name)
        if not target:
            return None
        raw_tags = await self._db.tags.get_tags_index(user_id, dek)
        for row in raw_tags:
            raw_name = str(row.get("name") or "").strip()
            if not raw_name:
                continue
            try:
                canonical = self.canonicalize(raw_name)
            except ValueError:
                continue
            if canonical != target:
                continue
            return TagIndexItem(
                name=self.display(canonical),
                hash=str(row.get("hash") or ""),
                count=int(row.get("count") or 0),
            )
        return None

    def _sort_index_items(
        self,
        items: list[TagIndexItem],
        *,
        sort_kind: TagsSortKind,
        sort_dir: TagsSortDirection,
    ) -> list[TagIndexItem]:
        if sort_kind == "count":
            if sort_dir == "desc":
                items.sort(key=lambda item: (-item.count, item.name))
            else:
                items.sort(key=lambda item: (item.count, item.name))
            return items
        if sort_dir == "desc":
            items.sort(key=lambda item: item.name, reverse=True)
        else:
            items.sort(key=lambda item: item.name)
        return items

    async def _build_archive_detail(
        self,
        user_id: str,
        dek: bytes,
        tag_item: TagIndexItem,
        *,
        limit: int,
        around_entry_id: str | None = None,
        secondary_tag_limit: int,
        related_tag_limit: int,
    ) -> TagArchiveDetail | None:
        try:
            tag_hash = bytes.fromhex(tag_item.hash)
        except ValueError:
            return None

        info = await self._db.tags.get_tag_info(user_id, tag_hash, dek)
        if not info:
            return None

        archive_entries, next_cursor, has_more = await self.get_archive_entries_page(
            user_id,
            [tag_hash],
            dek,
            limit=max(1, limit),
            around_entry_id=around_entry_id,
        )
        if not archive_entries:
            return TagArchiveDetail(
                name=tag_item.name,
                hash=tag_item.hash,
                count=int(info.get("count") or 0),
                first_used=info.get("first_used"),
                first_used_label=_format_month_year(info.get("first_used")),
                last_updated=info.get("last_updated"),
                summary_digest=_build_summary_digest(
                    int(info.get("count") or 0), info.get("last_updated")
                ),
                entries=(),
                entries_has_more=False,
                entries_next_cursor=None,
                related_tags=(),
            )
        related_counter: Counter[str] = Counter()
        for entry in archive_entries:
            for name in entry.related_tags:
                if name == tag_item.name:
                    continue
                related_counter[name] += 1

        related = tuple(
            TagRelatedItem(name=name, count=count)
            for name, count in sorted(
                related_counter.items(), key=lambda item: (-item[1], item[0])
            )[: max(0, related_tag_limit)]
        )
        return TagArchiveDetail(
            name=tag_item.name,
            hash=tag_item.hash,
            count=int(info.get("count") or tag_item.count),
            first_used=info.get("first_used"),
            first_used_label=_format_month_year(info.get("first_used")),
            last_updated=info.get("last_updated"),
            summary_digest=_build_summary_digest(
                int(info.get("count") or tag_item.count), info.get("last_updated")
            ),
            entries=tuple(archive_entries),
            entries_has_more=has_more,
            entries_next_cursor=next_cursor,
            related_tags=related,
        )

    async def get_archive_entries_page(
        self,
        user_id: str,
        tag_hashes: list[bytes],
        dek: bytes,
        *,
        limit: int,
        cursor: str | None = None,
        around_entry_id: str | None = None,
        secondary_tag_limit: int = 4,
    ) -> tuple[list[TagArchiveEntry], str | None, bool]:
        if around_entry_id and not cursor:
            rank = await self._db.tags.count_entries_newer_than(
                user_id, tag_hashes, around_entry_id
            )
            if rank is not None:
                limit = min(max(limit, rank + 5), 200)

        before_created_at, before_entry_id = _parse_tag_cursor(cursor)
        (
            entry_ids,
            next_cursor,
            has_more,
        ) = await self._db.tags.get_recent_entries_page_for_tag_hashes(
            user_id,
            tag_hashes,
            limit=max(1, limit),
            before_created_at=before_created_at,
            before_entry_id=before_entry_id,
        )
        if not entry_ids:
            return [], None, False

        entries = await self._db.entries.get_entries_by_ids(user_id, entry_ids, dek)
        entry_map = {entry.get("id"): entry for entry in entries}
        tags_by_entry = await self._db.tags.get_tags_for_entries(
            user_id, entry_ids, dek
        )
        hash_names: set[str] = set()
        if len(tag_hashes) == 1:
            info = await self._db.tags.get_tag_info(user_id, tag_hashes[0], dek)
            if info and info.get("name"):
                try:
                    hash_names.add(self.canonicalize(str(info["name"])))
                except ValueError:
                    pass

        archive_entries: list[TagArchiveEntry] = []
        user_entry_ids: list[str] = []
        for entry_id in entry_ids:
            entry = entry_map.get(entry_id)
            if not entry:
                continue
            created_at = str(entry.get("created_at") or "").strip()
            if not created_at:
                continue
            role = str(entry.get("role") or "")
            if role == "user":
                user_entry_ids.append(entry_id)

            secondary_tags: list[str] = []
            seen_secondary: set[str] = set()
            for tag in tags_by_entry.get(entry_id, []):
                raw_name = str(tag.get("name") or "").strip()
                if not raw_name:
                    continue
                try:
                    canonical = self.canonicalize(raw_name)
                except ValueError:
                    continue
                if canonical in hash_names or canonical in seen_secondary:
                    continue
                secondary_tags.append(canonical)
                seen_secondary.add(canonical)

            raw_text = str(entry.get("text") or "")
            text_html = render_markdown_to_html(raw_text)
            if not text_html:
                text_html = "<p>...</p>"

            archive_entries.append(
                TagArchiveEntry(
                    entry={
                        "id": entry_id,
                        "role": role,
                        "text": raw_text,
                        "text_html": text_html,
                        "meta": entry.get("meta", {}),
                        "tags": tuple(tags_by_entry.get(entry_id, [])),
                        "created_at": created_at,
                    },
                    created_date=entry.get("created_date"),
                    related_tags=tuple(secondary_tags[: max(1, secondary_tag_limit)]),
                    responses=(),
                )
            )

        responses_by_reply_to: dict[str, list[dict[str, Any]]] = {}
        reply_entries = await self._db.entries.get_entries_by_reply_to_ids(
            user_id,
            user_entry_ids,
            dek,
        )
        for response in reply_entries:
            reply_to = str(response.get("reply_to") or "").strip()
            created_at = str(response.get("created_at") or "").strip()
            if not reply_to or not created_at:
                continue
            if str(response.get("role") or "") == "user":
                continue
            response_html = render_markdown_to_html(str(response.get("text") or ""))
            if not response_html:
                response_html = "<p>...</p>"
            responses_by_reply_to.setdefault(reply_to, []).append(
                {
                    "id": str(response.get("id") or ""),
                    "role": str(response.get("role") or "assistant"),
                    "reply_to": reply_to,
                    "text": str(response.get("text") or ""),
                    "text_html": response_html,
                    "meta": response.get("meta", {}),
                    "created_at": created_at,
                }
            )

        if responses_by_reply_to:
            archive_entries = [
                TagArchiveEntry(
                    entry=item.entry,
                    created_date=item.created_date,
                    related_tags=item.related_tags,
                    responses=tuple(
                        responses_by_reply_to.get(str(item.entry.get("id") or ""), ())
                    ),
                )
                for item in archive_entries
            ]
        return archive_entries, next_cursor, has_more

    async def hydrate_search_results(
        self,
        user_id: str,
        dek: bytes,
        results: list[dict[str, Any]],
        tokens: Sequence[str],
        *,
        max_visible: int = 3,
    ) -> None:
        """Attach tag metadata to ``results`` for rendering."""

        if not results:
            return

        entry_ids: list[str] = []
        for res in results:
            entry_id = res.get("id")
            if isinstance(entry_id, str) and entry_id:
                entry_ids.append(entry_id)
        if not entry_ids:
            return

        token_lookup = {token.lower() for token in tokens}
        tag_map = await self._db.tags.get_tags_for_entries(user_id, entry_ids, dek)

        for res in results:
            entry_id = res.get("id")
            raw_tags = tag_map.get(entry_id, []) if entry_id else []
            prepared, visible, has_more = self._prepare_tags(
                raw_tags, token_lookup, max_visible=max_visible
            )
            res["tags"] = prepared
            res["visible_tags"] = visible
            res["has_more_tags"] = has_more

    async def suggest_for_entry(
        self,
        user_id: str,
        entry_id: str,
        dek: bytes,
        *,
        llm,
        query: str | None = None,
        limit: int | None = None,
        frecency_limit: int = 3,
        decay_constant: float | None = None,
    ) -> list[str] | None:
        """Return suggested tags for an entry."""

        entries = await self._db.entries.get_entries_by_ids(user_id, [entry_id], dek)
        if not entries:
            return None

        entry = entries[0]
        existing = await self._db.tags.get_tags_for_entry(user_id, entry_id, dek)
        existing_names = self._extract_existing_names(existing)

        query_value = str(query or "").strip()
        if query_value:
            matches = await self._db.tags.search_tags(
                user_id,
                dek,
                limit=limit or 12,
                prefix=query_value,
                lambda_=decay_constant,
                exclude_names=existing_names,
            )
            suggestions: list[str] = []
            for entry in matches:
                name = (entry.get("name") or "").strip()
                if not name:
                    continue
                try:
                    canonical = self.canonicalize(name)
                except ValueError:
                    continue
                if canonical.lower() in existing_names:
                    continue
                suggestions.append(canonical)
            return suggestions

        meta = entry.get("meta") or {}
        tags: Iterable[Any] = meta.get("tags") or []
        if (not tags) and entry.get("role") == "user":
            cached = self._get_cached_suggestions(user_id, entry_id)
            if cached is None:
                meta_payload = await generate_metadata(llm, entry.get("text", ""))
                tags = meta_payload.get("tags") or []
                self._set_cached_suggestions(user_id, entry_id, list(tags))
            else:
                tags = cached

        meta_suggestions: set[str] = set()
        for tag in tags:
            try:
                canonical = self.canonicalize(str(tag))
            except ValueError:
                continue
            meta_suggestions.add(canonical)

        frecent_tags = await self._db.tags.get_tag_frecency(
            user_id, frecency_limit, decay_constant, dek
        )
        frecent_suggestions: set[str] = set()
        for tag in frecent_tags:
            name = (tag.get("name") or "").strip()
            if not name:
                continue
            try:
                canonical = self.canonicalize(name)
            except ValueError:
                continue
            frecent_suggestions.add(canonical)

        combined = [
            suggestion
            for suggestion in sorted(meta_suggestions | frecent_suggestions)
            if suggestion.lower() not in existing_names
        ]

        if limit is not None and limit > 0:
            combined = combined[:limit]

        return combined

    def _get_cached_suggestions(self, user_id: str, entry_id: str) -> list[str] | None:
        cache_key = (user_id, entry_id)
        cached = self._suggestion_cache.get(cache_key)
        if not cached:
            return None
        timestamp, suggestions = cached
        if time.monotonic() - timestamp > self._suggestion_ttl:
            self._suggestion_cache.pop(cache_key, None)
            return None
        return list(suggestions)

    def _set_cached_suggestions(
        self, user_id: str, entry_id: str, suggestions: list[str]
    ) -> None:
        if len(self._suggestion_cache) > 256:
            self._suggestion_cache.clear()
        self._suggestion_cache[(user_id, entry_id)] = (
            time.monotonic(),
            list(suggestions),
        )

    def _extract_existing_names(self, existing: Sequence[dict[str, Any]]) -> set[str]:
        names: set[str] = set()
        for tag in existing:
            name = (tag.get("name") or "").strip()
            if not name:
                continue
            try:
                canonical = self.canonicalize(name)
            except ValueError:
                continue
            names.add(canonical.lower())
        return names

    def _prepare_tags(
        self,
        raw_tags: Sequence[dict[str, Any]],
        token_lookup: set[str],
        *,
        max_visible: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        prepared: list[dict[str, Any]] = []
        for tag in raw_tags:
            name = str(tag.get("name") or "").strip()
            if not name:
                continue
            try:
                canonical = self.canonicalize(name)
            except ValueError:
                canonical = name.strip()
            normalized = canonical.lower()
            prepared.append(
                {
                    "name": self.display(canonical),
                    "hash": tag.get("hash"),
                    "is_match": normalized in token_lookup,
                }
            )

        visible, has_more = self._select_visible_tags(prepared, max_visible=max_visible)
        return prepared, visible, has_more

    def _select_visible_tags(
        self, tags: Sequence[dict[str, Any]], *, max_visible: int
    ) -> tuple[list[dict[str, Any]], bool]:
        if not tags or max_visible <= 0:
            return [], bool(tags)

        indexes = list(range(len(tags)))
        selected = indexes[:max_visible]
        match_indexes = [idx for idx, tag in enumerate(tags) if tag.get("is_match")]

        if match_indexes:
            match_set = set(match_indexes)
            selected_set = set(selected)
            for idx in match_indexes:
                if idx in selected_set:
                    continue
                replaced = False
                for candidate in reversed(selected):
                    if candidate in match_set:
                        continue
                    selected.remove(candidate)
                    selected.append(idx)
                    selected_set.remove(candidate)
                    selected_set.add(idx)
                    replaced = True
                    break
                if not replaced:
                    continue

        selected.sort()
        visible = [tags[idx] for idx in selected]
        return list(visible), len(tags) > len(visible)


def _extract_preview_line(
    text: str,
    max_chars: int = 140,
    *,
    min_chars: int = 36,
    min_words: int = 4,
) -> str:
    if not text:
        return ""

    cleaned_lines: list[str] = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cleaned = _MARKDOWN_PREFIX.sub("", stripped).strip()
        if cleaned:
            cleaned_lines.append(cleaned)

    if not cleaned_lines:
        return ""

    parts: list[str] = []
    for line in cleaned_lines:
        parts.append(line)
        combined = " ".join(parts)
        words = combined.split()
        if len(combined) >= min_chars or len(words) >= min_words:
            break

    candidate = " ".join(parts)
    candidate = " ".join(candidate.split())
    full_text = " ".join(cleaned_lines)
    full_text = " ".join(full_text.split())

    match = _SENTENCE_END.search(full_text)
    sentence = full_text[: match.end()].strip() if match else full_text.strip()

    if len(candidate.split()) < min_words and len(sentence) > len(candidate):
        clean = sentence
    else:
        clean = candidate or sentence

    if len(clean) > max_chars:
        clean = textwrap.shorten(clean, width=max_chars, placeholder="...")
    return clean.strip()


def _extract_preview_excerpt(
    text: str, max_lines: int = 4, max_chars: int = 420
) -> str:
    if not text:
        return ""

    lines: list[str] = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cleaned = _MARKDOWN_PREFIX.sub("", stripped).strip()
        if not cleaned:
            continue
        lines.append(cleaned)
        if len(lines) >= max_lines:
            break

    if not lines:
        return ""

    excerpt = "\n".join(lines).strip()
    if len(excerpt) <= max_chars:
        return excerpt
    return textwrap.shorten(
        excerpt.replace("\n", " "), width=max_chars, placeholder="..."
    )


def _parse_datetime(raw: str | None) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_month_year(raw: str | None) -> str | None:
    dt = _parse_datetime(raw)
    if not dt:
        return None
    return dt.strftime("%B %Y")


def _format_month_day(created_at: str, created_date: str | None) -> str:
    date_value = str(created_date or "").strip()
    if date_value:
        try:
            date_dt = datetime.fromisoformat(f"{date_value}T00:00:00")
            return f"{date_dt.strftime('%B')} {date_dt.day}"
        except ValueError:
            pass
    created_dt = _parse_datetime(created_at)
    if created_dt:
        return f"{created_dt.strftime('%B')} {created_dt.day}"
    return "Unknown date"
