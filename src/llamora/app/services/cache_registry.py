"""Unified cache + digest registry for lockbox-backed caches.

Contract:
- Lockbox caches are best-effort and MUST be validated via digest lineage.
- Digest mismatches invalidate cached values (client + server).
- Invalidation must be expressed through this registry so keys and scopes stay
  consistent across services, routes, and clients.
"""

from __future__ import annotations

from dataclasses import dataclass

from llamora.app.services.tag_recall_cache import tag_recall_namespace

SUMMARY_NAMESPACE = "summary"
DIGEST_NAMESPACE = "digest"


@dataclass(frozen=True, slots=True)
class CacheInvalidation:
    namespace: str
    key: str | None = None
    prefix: str | None = None
    reason: str = "invalidate"
    scope: str = "both"

    def as_client_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {
            "namespace": self.namespace,
            "reason": self.reason,
        }
        if self.key:
            payload["key"] = self.key
        if self.prefix:
            payload["prefix"] = self.prefix
        return payload


def invalidate_day_summary(date: str, *, reason: str) -> CacheInvalidation:
    return CacheInvalidation(
        namespace=SUMMARY_NAMESPACE,
        prefix=f"day:{date}",
        reason=reason,
    )


def invalidate_tag_summary(tag_hash: str, *, reason: str) -> CacheInvalidation:
    return CacheInvalidation(
        namespace=SUMMARY_NAMESPACE,
        prefix=f"tag:{tag_hash}",
        reason=reason,
    )


def invalidate_day_digest(date: str, *, reason: str) -> CacheInvalidation:
    return CacheInvalidation(
        namespace=DIGEST_NAMESPACE,
        key=f"day:{date}",
        reason=reason,
        scope="server",
    )


def invalidate_tag_digest(tag_hash: str, *, reason: str) -> CacheInvalidation:
    return CacheInvalidation(
        namespace=DIGEST_NAMESPACE,
        key=f"tag:{tag_hash}",
        reason=reason,
        scope="server",
    )


def invalidate_tag_recall(tag_hash: str, *, reason: str) -> CacheInvalidation:
    return CacheInvalidation(
        namespace=tag_recall_namespace(tag_hash),
        prefix="",
        reason=reason,
        scope="server",
    )


def invalidations_for_entry_change(
    *, created_date: str, tag_hashes: list[str] | tuple[str, ...], reason: str
) -> list[CacheInvalidation]:
    items = [invalidate_day_summary(created_date, reason=reason)]
    for tag_hash in tag_hashes:
        items.append(invalidate_tag_summary(tag_hash, reason=reason))
    return items


def invalidations_for_tag_link(
    *, created_date: str | None, tag_hash: str, reason: str
) -> list[CacheInvalidation]:
    items = [invalidate_tag_summary(tag_hash, reason=reason)]
    if created_date:
        items.append(invalidate_day_summary(created_date, reason=reason))
    return items


def invalidations_for_tag_recall(
    tag_hash: str, *, reason: str
) -> list[CacheInvalidation]:
    return [invalidate_tag_recall(tag_hash, reason=reason)]


def to_client_payload(items: list[CacheInvalidation]) -> list[dict[str, str]]:
    return [
        item.as_client_payload() for item in items if item.scope in {"both", "client"}
    ]
