"""Presentation adapters for tag-oriented route/template rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llamora.app.services.markdown import render_markdown_to_html
from llamora.app.services.tag_service import (
    TagArchiveDetail,
    TagArchiveEntry,
    TagsViewData,
)


@dataclass(slots=True)
class PresentedTagArchiveEntry:
    entry: dict[str, Any]
    created_date: str | None
    related_tags: tuple[str, ...]
    responses: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class PresentedTagArchiveDetail:
    name: str
    hash: str
    count: int
    first_used: str | None
    first_used_label: str | None
    last_updated: str | None
    summary_digest: str
    entries: tuple[PresentedTagArchiveEntry, ...]
    related_tags: tuple[Any, ...]
    entries_has_more: bool = False
    entries_next_cursor: str | None = None


@dataclass(slots=True)
class PresentedTagsViewData:
    tags: tuple[Any, ...]
    selected_tag: str | None
    detail: PresentedTagArchiveDetail | None
    sort_kind: str
    sort_dir: str


def present_tags_view_data(tags_view: TagsViewData) -> PresentedTagsViewData:
    detail = present_archive_detail(tags_view.detail) if tags_view.detail else None
    return PresentedTagsViewData(
        tags=tags_view.tags,
        selected_tag=tags_view.selected_tag,
        detail=detail,
        sort_kind=tags_view.sort_kind,
        sort_dir=tags_view.sort_dir,
    )


def present_archive_entries(
    entries: list[TagArchiveEntry] | tuple[TagArchiveEntry, ...],
) -> list[PresentedTagArchiveEntry]:
    return [_present_archive_entry(item) for item in entries]


def present_archive_detail(detail: TagArchiveDetail) -> PresentedTagArchiveDetail:
    return PresentedTagArchiveDetail(
        name=detail.name,
        hash=detail.hash,
        count=detail.count,
        first_used=detail.first_used,
        first_used_label=detail.first_used_label,
        last_updated=detail.last_updated,
        summary_digest=detail.summary_digest,
        entries=tuple(_present_archive_entry(item) for item in detail.entries),
        related_tags=detail.related_tags,
        entries_has_more=detail.entries_has_more,
        entries_next_cursor=detail.entries_next_cursor,
    )


def _present_archive_entry(item: TagArchiveEntry) -> PresentedTagArchiveEntry:
    entry_text = item.entry.text
    entry_html = render_markdown_to_html(entry_text) or "<p>...</p>"
    responses: list[dict[str, Any]] = []
    for response in item.responses:
        response_html = render_markdown_to_html(response.text) or "<p>...</p>"
        responses.append(
            {
                "id": response.id,
                "role": response.role,
                "reply_to": response.reply_to,
                "text": response.text,
                "text_html": response_html,
                "meta": response.meta,
                "created_at": response.created_at,
            }
        )

    return PresentedTagArchiveEntry(
        entry={
            "id": item.entry.id,
            "role": item.entry.role,
            "text": entry_text,
            "text_html": entry_html,
            "meta": item.entry.meta,
            "tags": item.entry.tags,
            "created_at": item.entry.created_at,
        },
        created_date=item.created_date,
        related_tags=item.related_tags,
        responses=tuple(responses),
    )
