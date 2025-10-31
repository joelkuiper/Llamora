"""Helpers for working with tag identifiers."""

from __future__ import annotations

import hashlib

from config import MAX_TAG_LENGTH


_CANONICAL_LIMIT = max(0, MAX_TAG_LENGTH - 1)


def canonicalize(raw: str) -> str:
    """Return the canonical representation of a tag.

    A canonical tag:

    * trims surrounding whitespace
    * removes a single leading ``#`` if present
    * is truncated to the configured maximum length (excluding the ``#``)

    ``ValueError`` is raised when the resulting tag is empty.
    """

    value = (raw or "").strip()
    if value.startswith("#"):
        value = value[1:]
    value = value.strip()
    if not value:
        raise ValueError("Empty tag")
    if _CANONICAL_LIMIT:
        value = value[:_CANONICAL_LIMIT]
    if not value:
        raise ValueError("Empty tag")
    return value


def display(canonical: str) -> str:
    """Return the display form for a canonical tag."""

    name = (canonical or "").strip()
    if not name:
        return "#"
    return f"#{name}"


def tag_hash(user_id: str, canonical: str) -> bytes:
    """Return the hash identifier for ``canonical`` owned by ``user_id``."""

    normalized = canonicalize(canonical)
    display_name = display(normalized)
    return hashlib.sha256(f"{user_id}:{display_name}".encode("utf-8")).digest()
