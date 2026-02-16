"""Canonical digest policy helpers.

This module centralises digest aggregation rules so caches and summaries
stay stable across services.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

DIGEST_POLICY_VERSION = 1
"""Version for aggregate digest policy and derived cache inputs."""

ENTRY_DIGEST_VERSION = 2
"""Version stored with per-entry digests in the database."""

_EMPTY_DIGEST_LIST_TAG = "empty"


def digest_policy_tag() -> str:
    """Return the compact version tag for digest-policy derived values."""

    return f"dp{DIGEST_POLICY_VERSION}"


def _normalized_digests(entry_digests: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for digest in entry_digests:
        value = str(digest or "").strip()
        if value:
            cleaned.append(value)
    cleaned.sort()
    return cleaned


def entry_digest_aggregate(entry_digests: Iterable[str]) -> str:
    """Build a policy-tagged aggregate digest for entry digest lists.

    Rules:
    - Empty digest lists produce a tagged sentinel value.
    - Non-empty digest lists are sorted, joined with ``|``, hashed with SHA256,
      then tagged with the active digest-policy version.
    """

    normalized = _normalized_digests(entry_digests)
    policy_tag = digest_policy_tag()
    if not normalized:
        return f"{policy_tag}:{_EMPTY_DIGEST_LIST_TAG}"
    payload = "|".join(normalized).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"{policy_tag}:{digest}"


def day_digest(entry_digests: Iterable[str]) -> str:
    """Return the canonical digest for day summaries."""

    return entry_digest_aggregate(entry_digests)


def tag_digest(entry_digests: Iterable[str]) -> str:
    """Return the canonical digest for tag summaries."""

    return entry_digest_aggregate(entry_digests)


def recall_cache_digest_inputs(
    entry_digests: Iterable[str],
    *,
    max_chars: int,
    input_max_chars: int,
    max_snippets: int,
) -> str:
    """Return a stable digest for tag-recall cache inputs.

    Includes digest-policy version and normalised aggregate digest so cache keys
    automatically roll on policy changes.
    """

    payload = (
        f"{digest_policy_tag()}|"
        f"{entry_digest_aggregate(entry_digests)}|"
        f"{max_chars}|{input_max_chars}|{max_snippets}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "DIGEST_POLICY_VERSION",
    "ENTRY_DIGEST_VERSION",
    "digest_policy_tag",
    "entry_digest_aggregate",
    "day_digest",
    "tag_digest",
    "recall_cache_digest_inputs",
]
