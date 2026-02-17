"""Helpers for working with tag identifiers."""

from __future__ import annotations

import hashlib
import re
import unicodedata

import emoji as emoji_lib

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
_EMOJI_SHORTCODE = re.compile(r"^:[a-z0-9_+\-]+:$", re.IGNORECASE)


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


def _expand_emoji_shortcode(raw: str) -> str | None:
    value = str(raw or "").strip()
    if not value or not _EMOJI_SHORTCODE.fullmatch(value):
        return None
    expanded = emoji_lib.emojize(value, language="alias")
    if expanded != value:
        return expanded
    expanded = emoji_lib.emojize(value, language="en")
    if expanded != value:
        return expanded
    return None


def emoji_shortcode(value: str) -> str | None:
    """Return ``:shortcode:`` for an emoji tag when available."""

    emoji_value = _canonicalize_emoji_tag(value)
    if not emoji_value:
        return None
    short = emoji_lib.demojize(emoji_value, language="alias")
    if not short or short == emoji_value:
        short = emoji_lib.demojize(emoji_value, language="en")
    short = str(short or "").strip()
    if not short or short == emoji_value:
        return None
    if not _EMOJI_SHORTCODE.fullmatch(short):
        return None
    return short


def canonicalize(raw: str) -> str:
    """Return the canonical representation of a tag (kebab-case)."""

    raw_value = str(raw or "").strip()
    expanded = _expand_emoji_shortcode(raw_value)
    if expanded:
        emoji = _canonicalize_emoji_tag(expanded)
        if emoji:
            return emoji
    elif _EMOJI_SHORTCODE.fullmatch(raw_value):
        raise ValueError("Unknown emoji shortcode")

    emoji = _canonicalize_emoji_tag(raw_value)
    if emoji:
        return emoji

    value = raw_value.lower()
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
