"""Unified cache + digest registry for lockbox-backed caches.

Contract:
- Lockbox caches are best-effort and MUST be validated via digest lineage.
- Digest mismatches invalidate cached values (client + server).
- Invalidation must be expressed through this registry so keys and scopes stay
  consistent across services, routes, and clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

from llamora.app.services.tag_recall_cache import tag_recall_namespace

SUMMARY_NAMESPACE = "summary"
DIGEST_NAMESPACE = "digest"
HEATMAP_NAMESPACE = "heatmap"

MUTATION_ENTRY_CREATED = "entry.created"
MUTATION_ENTRY_CHANGED = "entry.changed"
MUTATION_ENTRY_DELETED = "entry.deleted"
MUTATION_TAG_LINK_CHANGED = "tag.link.changed"
MUTATION_TAG_DELETED = "tag.deleted"

DigestNodeKind = Literal["day", "tag"]


@dataclass(frozen=True, slots=True)
class DigestNode:
    kind: DigestNodeKind
    value: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"


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


@dataclass(frozen=True, slots=True)
class MutationLineagePlan:
    mutation: str
    reason: str
    digest_nodes: tuple[DigestNode, ...]
    invalidations: tuple[CacheInvalidation, ...]

    def client_payload(self) -> list[dict[str, str]]:
        return to_client_payload(self.invalidations)


@dataclass(frozen=True, slots=True)
class MutationLineageSpec:
    include_day_nodes: bool
    include_tag_nodes: bool
    include_tag_recall: bool


MUTATION_LINEAGE_GRAPH: dict[str, MutationLineageSpec] = {
    MUTATION_ENTRY_CREATED: MutationLineageSpec(
        include_day_nodes=True,
        include_tag_nodes=False,
        include_tag_recall=False,
    ),
    MUTATION_ENTRY_CHANGED: MutationLineageSpec(
        include_day_nodes=True,
        include_tag_nodes=True,
        include_tag_recall=True,
    ),
    MUTATION_ENTRY_DELETED: MutationLineageSpec(
        include_day_nodes=True,
        include_tag_nodes=True,
        include_tag_recall=True,
    ),
    MUTATION_TAG_LINK_CHANGED: MutationLineageSpec(
        include_day_nodes=True,
        include_tag_nodes=True,
        include_tag_recall=True,
    ),
    MUTATION_TAG_DELETED: MutationLineageSpec(
        include_day_nodes=True,
        include_tag_nodes=True,
        include_tag_recall=True,
    ),
}


def _normalize_tokens(values: Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _build_digest_nodes(
    *,
    mutation: str,
    created_dates: Sequence[str],
    tag_hashes: Sequence[str],
) -> tuple[DigestNode, ...]:
    spec = MUTATION_LINEAGE_GRAPH.get(mutation)
    if spec is None:
        raise ValueError(f"Unknown mutation type: {mutation}")

    nodes: list[DigestNode] = []
    if spec.include_day_nodes:
        nodes.extend(DigestNode(kind="day", value=value) for value in created_dates)
    if spec.include_tag_nodes:
        nodes.extend(DigestNode(kind="tag", value=value) for value in tag_hashes)
    return tuple(nodes)


def _invalidations_for_digest_node(
    node: DigestNode,
    *,
    reason: str,
    include_tag_recall: bool,
) -> tuple[CacheInvalidation, ...]:
    if node.kind == "day":
        return (
            invalidate_day_digest(node.value, reason=reason),
            invalidate_day_summary(node.value, reason=reason),
        )
    tag_items: list[CacheInvalidation] = [
        invalidate_tag_digest(node.value, reason=reason),
        invalidate_tag_summary(node.value, reason=reason),
        invalidate_tag_heatmap(node.value, reason=reason),
    ]
    if include_tag_recall:
        tag_items.append(invalidate_tag_recall(node.value, reason=reason))
    return tuple(tag_items)


def _dedupe_invalidations(
    items: Iterable[CacheInvalidation],
) -> tuple[CacheInvalidation, ...]:
    seen: set[tuple[str, str | None, str | None, str]] = set()
    deduped: list[CacheInvalidation] = []
    for item in items:
        key = (item.namespace, item.key, item.prefix, item.scope)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return tuple(deduped)


def build_mutation_lineage_plan(
    *,
    mutation: str,
    reason: str | None = None,
    created_dates: Sequence[str] | None = None,
    tag_hashes: Sequence[str] | None = None,
) -> MutationLineagePlan:
    """Build lineage for a mutation: mutation -> digest nodes -> cache keys."""

    normalized_days = _normalize_tokens(created_dates)
    normalized_tags = _normalize_tokens(tag_hashes)
    nodes = _build_digest_nodes(
        mutation=mutation,
        created_dates=normalized_days,
        tag_hashes=normalized_tags,
    )
    resolved_reason = str(reason or "").strip() or mutation
    spec = MUTATION_LINEAGE_GRAPH[mutation]

    invalidations: list[CacheInvalidation] = []
    for node in nodes:
        invalidations.extend(
            _invalidations_for_digest_node(
                node,
                reason=resolved_reason,
                include_tag_recall=spec.include_tag_recall,
            )
        )

    return MutationLineagePlan(
        mutation=mutation,
        reason=resolved_reason,
        digest_nodes=nodes,
        invalidations=_dedupe_invalidations(invalidations),
    )


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
        scope="both",
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


def heatmap_month_cache_key(tag_hash: str, year_month: str) -> str:
    return f"tag:{tag_hash}:month:{year_month}"


def invalidate_tag_heatmap(tag_hash: str, *, reason: str) -> CacheInvalidation:
    return CacheInvalidation(
        namespace=HEATMAP_NAMESPACE,
        prefix=f"tag:{tag_hash}:month:",
        reason=reason,
        scope="server",
    )


def invalidations_for_entry_change(
    *, created_date: str, tag_hashes: list[str] | tuple[str, ...], reason: str
) -> list[CacheInvalidation]:
    plan = build_mutation_lineage_plan(
        mutation=MUTATION_ENTRY_CHANGED,
        reason=reason,
        created_dates=(created_date,),
        tag_hashes=tag_hashes,
    )
    return list(plan.invalidations)


def invalidations_for_tag_link(
    *, created_date: str | None, tag_hash: str, reason: str
) -> list[CacheInvalidation]:
    plan = build_mutation_lineage_plan(
        mutation=MUTATION_TAG_LINK_CHANGED,
        reason=reason,
        created_dates=(created_date,) if created_date else (),
        tag_hashes=(tag_hash,),
    )
    return list(plan.invalidations)


def invalidations_for_tag_recall(
    tag_hash: str, *, reason: str
) -> list[CacheInvalidation]:
    return [invalidate_tag_recall(tag_hash, reason=reason)]


def to_client_payload(items: Iterable[CacheInvalidation]) -> list[dict[str, str]]:
    return [
        item.as_client_payload() for item in items if item.scope in {"both", "client"}
    ]
