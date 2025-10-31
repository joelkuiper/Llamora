"""Helpers for working with tag identifiers."""

from __future__ import annotations

import hashlib

from config import MAX_TAG_LENGTH


def canonicalize(raw: str) -> str:
    """Return the canonical representation of a tag."""

    value = str(raw or "").strip()
    if value.startswith("#"):
        value = value[1:].strip()
    if not value:
        raise ValueError("Empty tag")
    value = value[:MAX_TAG_LENGTH].strip()
    if not value:
        raise ValueError("Empty tag")
    return value


def display(canonical: str) -> str:
    """Return the canonical form for display without a prefix."""

    return str(canonical or "").strip()


def tag_hash(user_id: str, canonical: str) -> bytes:
    """Return the hash identifier for ``canonical`` owned by ``user_id``."""

    normalized = canonicalize(canonical)
    return hashlib.sha256(f"{user_id}:{normalized}".encode("utf-8")).digest()
