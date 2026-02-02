"""Utilities for server-side Markdown rendering."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import bleach
from bleach.sanitizer import Cleaner
from markdown_it import MarkdownIt
import mdit_py_plugins.tasklists as tasklists_module


@lru_cache
def _markdown_renderer() -> MarkdownIt:
    """Return a configured MarkdownIt renderer with GFM features enabled."""

    md = MarkdownIt("commonmark", {"linkify": True, "breaks": True})
    md.enable("table")
    md.enable("strikethrough")
    tasklists_plugin = getattr(tasklists_module, "tasklists_plugin", None)
    if tasklists_plugin is None:
        tasklists_plugin = getattr(tasklists_module, "tasklists")
    md.use(tasklists_plugin, enabled=True, label=True)
    return md


def _merge_attributes(
    base: dict[str, Iterable[str]], updates: dict[str, Iterable[str]]
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for key, values in base.items():
        merged[key] = sorted(set(values))
    for key, values in updates.items():
        combined = set(values)
        combined.update(merged.get(key, []))
        merged[key] = sorted(combined)
    return merged


@lru_cache
def _html_cleaner() -> Cleaner:
    """Return a reusable Bleach cleaner with entry-safe defaults."""

    allowed_tags = set(bleach.sanitizer.ALLOWED_TAGS)
    allowed_tags.update(
        {
            "p",
            "pre",
            "hr",
            "br",
            "div",
            "span",
            "code",
            "kbd",
            "s",
            "del",
            "ins",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
        }
    )

    allowed_attributes = _merge_attributes(
        dict(bleach.sanitizer.ALLOWED_ATTRIBUTES),
        {
            "*": ["class"],
            "a": ["rel", "title", "href"],
            "th": ["scope"],
            "td": ["colspan", "rowspan", "align"],
            "code": ["class"],
            "span": ["class"],
            "div": ["class"],
            "input": ["type", "checked", "disabled"],
        },
    )

    return Cleaner(
        tags=sorted(allowed_tags),
        attributes=allowed_attributes,
        protocols=["http", "https", "mailto", "tel"],
        strip=True,
        strip_comments=True,
    )


def render_markdown_to_html(markdown: str) -> str:
    """Render ``markdown`` text to sanitized HTML suitable for templates."""

    renderer = _markdown_renderer()
    cleaner = _html_cleaner()
    html = renderer.render(markdown or "")
    sanitized = cleaner.clean(html)
    return sanitized
