from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from app.api.search import SearchAPI


logger = logging.getLogger(__name__)


Job = Tuple[str, str, str, bytes]


DEFAULT_MAX_QUEUE_SIZE = 1024
DEFAULT_ENQUEUE_TIMEOUT = 5.0


class IndexWorker:
    """Background worker that indexes messages for search."""

    def __init__(
        self,
        search_api: "SearchAPI",
        *,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        enqueue_timeout: float | None = DEFAULT_ENQUEUE_TIMEOUT,
    ) -> None:
        self._search_api = search_api
        self._queue: asyncio.Queue[Job | None] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task | None = None
        self._enqueue_timeout = enqueue_timeout
        self._backpressure_events = 0
        self._dropped_jobs = 0

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

    async def enqueue(
        self, user_id: str, message_id: str, plaintext: str, dek: bytes
    ) -> None:
        """Queue a message for indexing."""
        try:
            self._queue.put_nowait((user_id, message_id, plaintext, dek))
        except asyncio.QueueFull:
            job = (user_id, message_id, plaintext, dek)
            self._backpressure_events += 1
            logger.warning(
                "Index queue full (%s/%s); waiting up to %s seconds",
                self._queue.qsize(),
                self._queue.maxsize,
                "infinite" if self._enqueue_timeout is None else self._enqueue_timeout,
            )
            if self._enqueue_timeout is None:
                await self._queue.put(job)
                return
            put_task = asyncio.create_task(self._queue.put(job))
            try:
                await asyncio.wait_for(put_task, timeout=self._enqueue_timeout)
            except asyncio.TimeoutError:
                self._dropped_jobs += 1
                logger.error(
                    "Dropping indexing job after %.2fs wait; queue still full (%s/%s)",
                    self._enqueue_timeout,
                    self._queue.qsize(),
                    self._queue.maxsize,
                )
                put_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await put_task

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

    @property
    def backpressure_events(self) -> int:
        """Number of times enqueue encountered a full queue."""

        return self._backpressure_events

    @property
    def dropped_jobs(self) -> int:
        """Number of jobs dropped because the queue remained full."""

        return self._dropped_jobs
