from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Tuple, TypedDict

EventHandler = Callable[..., Awaitable[None]]

ENTRY_INSERTED_EVENT = "entry.inserted"
ENTRY_UPDATED_EVENT = "entry.updated"
ENTRY_DELETED_EVENT = "entry.deleted"
TAG_LINKED_EVENT = "tag.linked"
TAG_UNLINKED_EVENT = "tag.unlinked"
TAG_DELETED_EVENT = "tag.deleted"


class EntryEventPayload(TypedDict):
    user_id: str
    entry_id: str
    created_date: str
    revision: int


class TagMutationPayload(TypedDict):
    user_id: str
    entry_id: str
    tag_hash: str
    created_date: str | None


class TagDeletedPayload(TypedDict):
    user_id: str
    tag_hash: str
    affected_entries: tuple[tuple[str, str | None], ...]


class RepositoryEventBus:
    """Simple async event bus for cross-repository notifications.

    Handlers registered with ``background=True`` are spawned as fire-and-forget
    tasks instead of being awaited inline, so they never block the emitter.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Tuple[EventHandler, bool]]] = defaultdict(list)
        self._background_tasks: set[asyncio.Task] = set()
        self._entry_revisions: dict[tuple[str, str], int] = {}
        self._revision_lock = asyncio.Lock()
        self._logger = logging.getLogger(__name__)

    def subscribe(
        self, event: str, handler: EventHandler, *, background: bool = False
    ) -> None:
        """Register a handler to be invoked when *event* is emitted.

        When *background* is ``True`` the handler is spawned as an
        ``asyncio.Task`` rather than awaited inline.
        """
        self._handlers[event].append((handler, background))

    def subscribe_for_user(
        self,
        event: str,
        user_id: str,
        handler: EventHandler,
        *,
        background: bool = False,
    ) -> None:
        """Register *handler* for a specific ``(event, user_id)`` combination."""
        self.subscribe(self._user_event(event, user_id), handler, background=background)

    def subscribe_for_user_date(
        self,
        event: str,
        user_id: str,
        created_date: str,
        handler: EventHandler,
        *,
        background: bool = False,
    ) -> None:
        """Register *handler* for a specific ``(event, user_id, created_date)``."""
        self.subscribe(
            self._user_date_event(event, user_id, created_date),
            handler,
            background=background,
        )

    async def emit(self, event: str, *args, **kwargs) -> None:
        """Emit *event*, await inline handlers and spawn background ones."""
        handlers = list(self._handlers.get(event, ()))
        if not handlers:
            return

        inline: list[Coroutine[Any, Any, None]] = []
        for handler, bg in handlers:
            coro = handler(*args, **kwargs)
            if bg:
                task = asyncio.create_task(coro)
                self._background_tasks.add(task)
                task.add_done_callback(self._task_done)
            else:
                inline.append(coro)

        if inline:
            results = await asyncio.gather(*inline, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    self._logger.error(
                        "Repository event handler failed for event '%s'",
                        event,
                        exc_info=result,
                    )

    async def emit_for_entry_date(
        self, event: str, *, user_id: str, created_date: str, **payload
    ) -> None:
        """Emit *event* at multiple granularities for an entry date.

        The event is emitted three times in parallel:

        * ``event`` – for listeners interested in all occurrences.
        * ``f"{event}:{user_id}"`` – for listeners scoped to a user.
        * ``f"{event}:{user_id}:{created_date}"`` – for listeners scoped to a
          specific user and day.

        A monotonic ``revision`` is attached to each emission and incremented per
        ``(user_id, created_date)`` pair. Subscribers should treat ``revision`` as
        the canonical ordering source for that day and ignore stale payloads with
        lower revisions.
        """

        revision = await self._next_entry_revision(user_id, created_date)
        data = {
            "user_id": user_id,
            "created_date": created_date,
            "revision": revision,
            **payload,
        }
        await asyncio.gather(
            self.emit(event, **data),
            self.emit(self._user_event(event, user_id), **data),
            self.emit(self._user_date_event(event, user_id, created_date), **data),
        )

    async def _next_entry_revision(self, user_id: str, created_date: str) -> int:
        key = (user_id, created_date)
        async with self._revision_lock:
            next_revision = self._entry_revisions.get(key, 0) + 1
            self._entry_revisions[key] = next_revision
        return next_revision

    def _task_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception():
            self._logger.error(
                "Background event handler failed",
                exc_info=task.exception(),
            )

    async def drain(self, timeout: float = 5.0) -> None:
        """Await all in-flight background tasks (for clean shutdown)."""
        if not self._background_tasks:
            return
        _done, pending = await asyncio.wait(self._background_tasks, timeout=timeout)
        for task in pending:
            task.cancel()

    def clear(self) -> None:
        """Remove all registered handlers and reset in-memory revisions."""
        self._handlers.clear()
        self._entry_revisions.clear()

    @staticmethod
    def _user_event(event: str, user_id: str) -> str:
        return f"{event}:{user_id}"

    @staticmethod
    def _user_date_event(event: str, user_id: str, created_date: str) -> str:
        return f"{event}:{user_id}:{created_date}"
