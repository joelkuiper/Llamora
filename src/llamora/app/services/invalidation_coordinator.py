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
from llamora.app.services.history_cache import HistoryCache
from llamora.app.services.lockbox_store import LockboxStore
from llamora.app.services.service_pulse import ServicePulse

logger = getLogger(__name__)


@dataclass(slots=True)
class InvalidationCoordinator:
    """Centralized repository-event invalidation handlers."""

    event_bus: RepositoryEventBus
    history_cache: HistoryCache | None
    lockbox_store: LockboxStore
    service_pulse: ServicePulse | None = None

    def subscribe(self) -> None:
        self.event_bus.subscribe(ENTRY_INSERTED_EVENT, self._on_entry_changed)
        self.event_bus.subscribe(ENTRY_UPDATED_EVENT, self._on_entry_changed)
        self.event_bus.subscribe(ENTRY_DELETED_EVENT, self._on_entry_changed)
        self.event_bus.subscribe(TAG_LINKED_EVENT, self._on_tag_link_changed)
        self.event_bus.subscribe(TAG_UNLINKED_EVENT, self._on_tag_link_changed)
        self.event_bus.subscribe(TAG_DELETED_EVENT, self._on_tag_deleted)

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
        await self._invalidate_history(
            user_id=user_id,
            created_date=created_date,
            revision=revision,
            cause="entry.changed",
            entry_id=entry_id,
        )
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
        if created_date:
            await self._invalidate_history(
                user_id=user_id,
                created_date=created_date,
                revision=None,
                cause="tag.link.changed",
                entry_id=entry_id,
                tag_hash=tag_hash,
            )
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
        dates: set[str] = {
            created_date for _, created_date in affected_entries if created_date
        }
        for created_date in sorted(dates):
            await self._invalidate_history(
                user_id=user_id,
                created_date=created_date,
                revision=None,
                cause="tag.deleted",
                tag_hash=tag_hash,
            )
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

    async def _invalidate_history(
        self,
        *,
        user_id: str,
        created_date: str,
        revision: str | None,
        cause: str,
        **extra: object,
    ) -> None:
        if self.history_cache is not None:
            await self.history_cache.invalidate(
                user_id,
                created_date,
                revision=revision,
            )
        self._pulse(
            "history_cache",
            user_id=user_id,
            created_date=created_date,
            revision=revision,
            cause=cause,
            **extra,
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
        for item in items:
            if item.scope not in {"both", "server"}:
                continue
            if item.key:
                await self.lockbox_store.delete(user_id, item.namespace, item.key)
                continue
            if item.prefix is None:
                continue
            if item.prefix == "":
                await self.lockbox_store.delete_namespace(user_id, item.namespace)
            else:
                await self.lockbox_store.delete_prefix(
                    user_id, item.namespace, item.prefix
                )

    def _pulse(self, action: str, **payload: object) -> None:
        event_payload = {"action": action, **payload}
        if self.service_pulse is not None:
            self.service_pulse.emit("cache.invalidation", event_payload)
        logger.debug("Invalidation action=%s payload=%r", action, event_payload)


__all__ = ["InvalidationCoordinator"]
