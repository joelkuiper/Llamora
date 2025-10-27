from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from app.api.search import SearchAPI


logger = logging.getLogger(__name__)


Job = Tuple[str, str, str, bytes]


class IndexWorker:
    """Background worker that indexes messages for search."""

    def __init__(self, search_api: "SearchAPI", *, max_queue_size: int = 0) -> None:
        self._search_api = search_api
        self._queue: asyncio.Queue[Job | None] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start processing indexing jobs."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="index-worker")

    async def stop(self) -> None:
        """Stop the worker after draining pending jobs."""
        if not self._task:
            return
        await self._queue.put(None)
        try:
            await self._task
        except asyncio.CancelledError:  # pragma: no cover - defensive
            pass
        finally:
            self._task = None

    async def enqueue(self, user_id: str, message_id: str, plaintext: str, dek: bytes) -> None:
        """Queue a message for indexing."""
        await self._queue.put((user_id, message_id, plaintext, dek))

    async def _run(self) -> None:
        maxsize = self._queue.maxsize
        while True:
            job = await self._queue.get()
            if job is None:
                self._queue.task_done()
                break
            user_id, message_id, plaintext, dek = job
            try:
                await self._search_api.on_message_appended(
                    user_id, message_id, plaintext, dek
                )
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "Failed to index message %s for user %s", message_id, user_id
                )
            finally:
                self._queue.task_done()
        # Drain any remaining sentinels.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - defensive
                break
            else:
                self._queue.task_done()
        self._queue = asyncio.Queue(maxsize=maxsize)
