"""Bridge repository mutation events into cache invalidation side effects.

Event flow:
1) Repositories emit typed events via ``RepositoryEventBus.emit_for_entry_date``.
2) ``InvalidationCoordinator`` subscribes to those event types and normalizes
   event payloads to mutation lineage plans.
3) The lineage plan (mutation -> digest nodes -> cache invalidations) is built
   via ``cache_registry.build_mutation_lineage_plan``.
4) Server-scope invalidations are applied to lockbox cache keys; routes emit
   client invalidation payloads separately from the same lineage model.

Why this layer exists:
- Repositories stay storage-focused and unaware of cache namespaces/lineage.
- Cache registry stays pure policy/data; this coordinator owns runtime wiring
  to event bus subscriptions and lockbox side effects.
- Event-type filtering and payload normalization are centralized in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from logging import getLogger

from llamora.app.db.events import (
    ENTRY_DELETED_EVENT,
    ENTRY_INSERTED_EVENT,
    ENTRY_UPDATED_EVENT,
    TAG_DELETED_EVENT,
    TAG_LINKED_EVENT,
    TAG_UNLINKED_EVENT,
    RepositoryEventBus,
)
from llamora.app.services.cache_registry import (
    CacheInvalidation,
    MUTATION_ENTRY_CHANGED,
    MUTATION_TAG_DELETED,
    MUTATION_TAG_LINK_CHANGED,
    MutationLineagePlan,
    build_mutation_lineage_plan,
)
from llamora.app.services.lockbox_store import LockboxStore
from llamora.app.services.service_pulse import ServicePulse
from llamora.app.services.tag_service import TagService

logger = getLogger(__name__)


@dataclass(slots=True)
class InvalidationCoordinator:
    """Runtime adapter from repository events to cache invalidation actions."""

    event_bus: RepositoryEventBus
    lockbox_store: LockboxStore
    service_pulse: ServicePulse | None = None
    tag_service: TagService | None = None

    def subscribe(self) -> None:
        """Wire supported repository events to coordinator handlers.

        Mapping:
        - ``entry.inserted``, ``entry.updated``, ``entry.deleted`` -> entry lineage
        - ``tag.linked``, ``tag.unlinked`` -> tag-link lineage
        - ``tag.deleted`` -> tag-deleted lineage
        """

        subscriptions = (
            (ENTRY_INSERTED_EVENT, self._on_entry_changed),
            (ENTRY_UPDATED_EVENT, self._on_entry_changed),
            (ENTRY_DELETED_EVENT, self._on_entry_changed),
            (TAG_LINKED_EVENT, self._on_tag_link_changed),
            (TAG_UNLINKED_EVENT, self._on_tag_link_changed),
            (TAG_DELETED_EVENT, self._on_tag_deleted),
        )
        for event_name, handler in subscriptions:
            self.event_bus.subscribe(event_name, handler)

    async def _on_entry_changed(
        self,
        *,
        user_id: str,
        created_date: str,
        revision: str,
        entry_id: str,
        entry: dict | None = None,
        tag_hashes: tuple[str, ...] | list[str] | None = None,
        **_: object,
    ) -> None:
        await self._apply_lineage(
            user_id=user_id,
            plan=build_mutation_lineage_plan(
                mutation=MUTATION_ENTRY_CHANGED,
                reason="entry.changed",
                created_dates=(created_date,),
                tag_hashes=self._extract_tag_hashes(entry, tag_hashes),
            ),
            entry_id=entry_id,
        )

    async def _on_tag_link_changed(
        self,
        *,
        user_id: str,
        entry_id: str,
        tag_hash: str,
        created_date: str | None = None,
    ) -> None:
        if self.tag_service is not None:
            self.tag_service.invalidate_tag_index(user_id)
        await self._apply_lineage(
            user_id=user_id,
            plan=build_mutation_lineage_plan(
                mutation=MUTATION_TAG_LINK_CHANGED,
                reason="tag.link.changed",
                created_dates=(created_date,) if created_date else (),
                tag_hashes=(tag_hash,),
            ),
            entry_id=entry_id,
            created_date=created_date,
            tag_hash=tag_hash,
        )

    async def _on_tag_deleted(
        self,
        *,
        user_id: str,
        tag_hash: str,
        affected_entries: tuple[tuple[str, str | None], ...],
    ) -> None:
        if self.tag_service is not None:
            self.tag_service.invalidate_tag_index(user_id)
        dates: set[str] = {
            created_date for _, created_date in affected_entries if created_date
        }
        await self._apply_lineage(
            user_id=user_id,
            plan=build_mutation_lineage_plan(
                mutation=MUTATION_TAG_DELETED,
                reason="tag.deleted",
                created_dates=tuple(sorted(dates)),
                tag_hashes=(tag_hash,),
            ),
            affected_entries=len(affected_entries),
            tag_hash=tag_hash,
        )

    async def _apply_lineage(
        self,
        *,
        user_id: str,
        plan: MutationLineagePlan,
        **extra: object,
    ) -> None:
        await self._apply_lockbox_invalidations(user_id, list(plan.invalidations))
        self._pulse(
            "lineage",
            user_id=user_id,
            mutation=plan.mutation,
            reason=plan.reason,
            digest_nodes=[node.key for node in plan.digest_nodes],
            **extra,
        )

    @staticmethod
    def _extract_tag_hashes(
        entry: dict | None, tag_hashes: tuple[str, ...] | list[str] | None
    ) -> tuple[str, ...]:
        hashes: list[str] = []
        seen: set[str] = set()
        for raw in tag_hashes or ():
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            hashes.append(value)
        if not isinstance(entry, dict):
            return tuple(hashes)
        raw_tags = entry.get("tags")
        if not isinstance(raw_tags, list):
            return tuple(hashes)
        for item in raw_tags:
            if not isinstance(item, dict):
                continue
            value = str(item.get("hash") or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            hashes.append(value)
        return tuple(hashes)

    async def _apply_lockbox_invalidations(
        self, user_id: str, items: list[CacheInvalidation]
    ) -> None:
        ops: list[tuple[str, str | None, str | None]] = []
        for item in items:
            if item.scope not in {"both", "server"}:
                continue
            if item.key:
                ops.append((item.namespace, item.key, None))
            elif item.prefix is not None:
                ops.append((item.namespace, None, item.prefix))
        if ops:
            await self.lockbox_store.delete_bulk(user_id, ops)

    def _pulse(self, action: str, **payload: object) -> None:
        event_payload = {"action": action, **payload}
        if self.service_pulse is not None:
            self.service_pulse.emit("cache.invalidation", event_payload)
        logger.debug("Invalidation action=%s payload=%r", action, event_payload)


__all__ = ["InvalidationCoordinator"]
