"""Service helpers for working with user tags."""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from llamora.app.util.tags import canonicalize as _canonicalize, display as _display
from llamora.persistence.local_db import LocalDB


logger = logging.getLogger(__name__)


class TagService:
    """Provide higher level helpers for tag canonicalisation and hydration."""

    def __init__(self, db: LocalDB) -> None:
        self._db = db

    def canonicalize(self, raw: str) -> str:
        """Return the canonical representation for ``raw``."""

        return _canonicalize(raw)

    def display(self, canonical: str) -> str:
        """Return the display-ready form for ``canonical``."""

        return _display(canonical)

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

        message_ids: list[str] = []
        for res in results:
            msg_id = res.get("id")
            if isinstance(msg_id, str) and msg_id:
                message_ids.append(msg_id)
        if not message_ids:
            return

        token_lookup = {token.lower() for token in tokens}
        tag_map = await self._db.tags.get_tags_for_messages(user_id, message_ids, dek)

        for res in results:
            msg_id = res.get("id")
            raw_tags = tag_map.get(msg_id, []) if msg_id else []
            prepared, visible, has_more = self._prepare_tags(
                raw_tags, token_lookup, max_visible=max_visible
            )
            res["tags"] = prepared
            res["visible_tags"] = visible
            res["has_more_tags"] = has_more

    async def suggest_for_message(
        self,
        user_id: str,
        msg_id: str,
        dek: bytes,
        *,
        frecency_limit: int = 3,
        decay_constant: float | None = None,
    ) -> list[str] | None:
        """Return suggested tags derived from metadata and frecency."""

        messages = await self._db.messages.get_messages_by_ids(user_id, [msg_id], dek)
        if not messages:
            return None

        message = messages[0]
        meta = message.get("meta") or {}
        keywords: Iterable[Any] = meta.get("keywords") or []
        existing = await self._db.tags.get_tags_for_message(user_id, msg_id, dek)
        existing_names = self._extract_existing_names(existing)

        meta_suggestions: set[str] = set()
        for keyword in keywords:
            try:
                canonical = self.canonicalize(str(keyword))
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

        return combined

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
