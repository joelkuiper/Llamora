from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List

EventHandler = Callable[..., Awaitable[None]]

MESSAGE_TAGS_CHANGED_EVENT = "message.tags.changed"


class RepositoryEventBus:
    """Simple async event bus for cross-repository notifications."""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._logger = logging.getLogger(__name__)

    def subscribe(self, event: str, handler: EventHandler) -> None:
        """Register a handler to be invoked when *event* is emitted."""
        self._handlers[event].append(handler)

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

    def clear(self) -> None:
        """Remove all registered handlers."""
        self._handlers.clear()
