from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List

EventHandler = Callable[..., Awaitable[None]]

ENTRY_TAGS_CHANGED_EVENT = "entry.tags.changed"
ENTRY_HISTORY_CHANGED_EVENT = "entry.history.changed"


class RepositoryEventBus:
    """Simple async event bus for cross-repository notifications."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._logger = logging.getLogger(__name__)

    def subscribe(self, event: str, handler: EventHandler) -> None:
        """Register a handler to be invoked when *event* is emitted."""
        self._handlers[event].append(handler)

    def subscribe_for_user(
        self, event: str, user_id: str, handler: EventHandler
    ) -> None:
        """Register *handler* for a specific ``(event, user_id)`` combination."""
        self.subscribe(self._user_event(event, user_id), handler)

    def subscribe_for_user_date(
        self, event: str, user_id: str, created_date: str, handler: EventHandler
    ) -> None:
        """Register *handler* for a specific ``(event, user_id, created_date)``."""
        self.subscribe(self._user_date_event(event, user_id, created_date), handler)

    async def emit(self, event: str, *args, **kwargs) -> None:
        """Emit *event* and await all registered handlers."""
        handlers = list(self._handlers.get(event, ()))
        if not handlers:
            return

        coroutines = [handler(*args, **kwargs) for handler in handlers]
        results = await asyncio.gather(*coroutines, return_exceptions=True)

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

        The event is emitted three times:

        * ``event`` – for listeners interested in all occurrences.
        * ``f"{event}:{user_id}"`` – for listeners scoped to a user.
        * ``f"{event}:{user_id}:{created_date}"`` – for listeners scoped to a
          specific user and day.
        """

        data = {"user_id": user_id, "created_date": created_date, **payload}
        await self.emit(event, **data)
        await self.emit(self._user_event(event, user_id), **data)
        await self.emit(self._user_date_event(event, user_id, created_date), **data)

    def clear(self) -> None:
        """Remove all registered handlers."""
        self._handlers.clear()

    @staticmethod
    def _user_event(event: str, user_id: str) -> str:
        return f"{event}:{user_id}"

    @staticmethod
    def _user_date_event(event: str, user_id: str, created_date: str) -> str:
        return f"{event}:{user_id}:{created_date}"
