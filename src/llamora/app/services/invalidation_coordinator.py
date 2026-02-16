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
from llamora.app.services.history_cache import HistoryCache
from llamora.app.services.lockbox_store import LockboxStore
from llamora.app.services.service_pulse import ServicePulse
from llamora.app.services.tag_recall_cache import tag_recall_namespace

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
        **_: object,
    ) -> None:
        await self._invalidate_history(
            user_id=user_id,
            created_date=created_date,
            revision=revision,
            cause="entry.changed",
            entry_id=entry_id,
        )
        await self._invalidate_day_digest(
            user_id=user_id,
            created_date=created_date,
            cause="entry.changed",
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
            await self._invalidate_day_digest(
                user_id=user_id,
                created_date=created_date,
                cause="tag.link.changed",
                entry_id=entry_id,
                tag_hash=tag_hash,
            )
        await self._invalidate_tag_digest(
            user_id=user_id,
            tag_hash=tag_hash,
            cause="tag.link.changed",
            entry_id=entry_id,
        )
        await self._invalidate_tag_recall(
            user_id=user_id,
            tag_hash=tag_hash,
            cause="tag.link.changed",
            entry_id=entry_id,
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
            await self._invalidate_day_digest(
                user_id=user_id,
                created_date=created_date,
                cause="tag.deleted",
                tag_hash=tag_hash,
            )
        await self._invalidate_tag_digest(
            user_id=user_id,
            tag_hash=tag_hash,
            cause="tag.deleted",
            affected_entries=len(affected_entries),
        )
        await self._invalidate_tag_recall(
            user_id=user_id,
            tag_hash=tag_hash,
            cause="tag.deleted",
            affected_entries=len(affected_entries),
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

    async def _invalidate_day_digest(
        self,
        *,
        user_id: str,
        created_date: str,
        cause: str,
        **extra: object,
    ) -> None:
        key = f"day:{created_date}"
        await self.lockbox_store.delete(user_id, "digest", key)
        self._pulse(
            "day_digest",
            user_id=user_id,
            created_date=created_date,
            key=key,
            cause=cause,
            **extra,
        )

    async def _invalidate_tag_digest(
        self,
        *,
        user_id: str,
        tag_hash: str,
        cause: str,
        **extra: object,
    ) -> None:
        key = f"tag:{tag_hash}"
        await self.lockbox_store.delete(user_id, "digest", key)
        self._pulse(
            "tag_digest",
            user_id=user_id,
            tag_hash=tag_hash,
            key=key,
            cause=cause,
            **extra,
        )

    async def _invalidate_tag_recall(
        self,
        *,
        user_id: str,
        tag_hash: str,
        cause: str,
        **extra: object,
    ) -> None:
        namespace = tag_recall_namespace(tag_hash)
        await self.lockbox_store.delete_namespace(user_id, namespace)
        self._pulse(
            "tag_recall",
            user_id=user_id,
            tag_hash=tag_hash,
            namespace=namespace,
            cause=cause,
            **extra,
        )

    def _pulse(self, action: str, **payload: object) -> None:
        event_payload = {"action": action, **payload}
        if self.service_pulse is not None:
            self.service_pulse.emit("cache.invalidation", event_payload)
        logger.debug("Invalidation action=%s payload=%r", action, event_payload)


__all__ = ["InvalidationCoordinator"]
