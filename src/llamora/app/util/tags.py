"""Helpers for working with tag identifiers."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from llamora.settings import settings


_NON_TAG_CHARS = re.compile(r"[^a-z0-9-]+")
_MULTI_DASH = re.compile(r"-{2,}")

# Emoji tags: allow a single emoji sequence (including ZWJ sequences, skin tones,
# flags, and keycaps). We keep this intentionally conservative: we do not
# broaden tags to arbitrary Unicode words.
_VS16 = 0xFE0F
_VS15 = 0xFE0E
_ZWJ = 0x200D
_KEYCAP = 0x20E3


def _is_emoji_base_codepoint(cp: int) -> bool:
    # Common emoji blocks / ranges.
    if 0x1F000 <= cp <= 0x1FAFF:
        return True
    if 0x2600 <= cp <= 0x27BF:
        return True
    if 0x2300 <= cp <= 0x23FF:
        return True
    if 0x2190 <= cp <= 0x21FF:
        return True
    if 0x2B00 <= cp <= 0x2BFF:
        return True
    if 0x25A0 <= cp <= 0x25FF:
        return True
    # © ® ™ are common emoji-like symbols.
    if cp in (0x00A9, 0x00AE, 0x2122):
        return True
    return False


def _canonicalize_emoji_tag(raw: str) -> str | None:
    value = unicodedata.normalize("NFC", str(raw or "").strip())
    if not value:
        return None
    # Emoji tags must be a single token.
    if any(ch.isspace() for ch in value):
        return None

    has_emoji = False
    has_keycap = False
    has_keycap_base = False
    cleaned: list[str] = []

    for ch in value:
        cp = ord(ch)
        if cp in (_VS15, _VS16):
            # Normalize away variation selectors so equivalent emoji map to one tag.
            continue
        if cp == _ZWJ:
            cleaned.append(ch)
            continue
        if cp == _KEYCAP:
            has_keycap = True
            cleaned.append(ch)
            continue
        if 0x1F3FB <= cp <= 0x1F3FF:
            # Skin tone modifiers.
            cleaned.append(ch)
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:
            # Regional indicators for flags.
            has_emoji = True
            cleaned.append(ch)
            continue
        if ch in ("#", "*") or ("0" <= ch <= "9"):
            # Keycap bases (e.g., 1️⃣, #️⃣, *️⃣).
            has_keycap_base = True
            cleaned.append(ch)
            continue
        if _is_emoji_base_codepoint(cp):
            has_emoji = True
            cleaned.append(ch)
            continue
        return None

    if not (has_emoji or (has_keycap and has_keycap_base)):
        return None

    result = "".join(cleaned).strip()
    if not result:
        return None

    max_length = int(settings.LIMITS.max_tag_length)
    # Slice by codepoints (safe for Python strings). This can cut complex emoji
    # sequences, but we prefer enforcing a hard limit over accepting arbitrary
    # long tags.
    if len(result) > max_length:
        result = result[:max_length].strip()
    return result or None


def canonicalize(raw: str) -> str:
    """Return the canonical representation of a tag (kebab-case)."""

    emoji = _canonicalize_emoji_tag(raw)
    if emoji:
        return emoji

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
