"""Helpers for working with tag identifiers."""

from __future__ import annotations

import hashlib
import re

from llamora.settings import settings


_NON_TAG_CHARS = re.compile(r"[^a-z0-9-]+")
_MULTI_DASH = re.compile(r"-{2,}")


def canonicalize(raw: str) -> str:
    """Return the canonical representation of a tag (kebab-case)."""

    value = str(raw or "").strip().lower()
    if not value:
        raise ValueError("Empty tag")
    value = re.sub(r"[\s_]+", "-", value)
    value = _NON_TAG_CHARS.sub("", value)
    value = _MULTI_DASH.sub("-", value).strip("-")
    max_length = int(settings.LIMITS.max_tag_length)
    value = value[:max_length].strip("-")
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
