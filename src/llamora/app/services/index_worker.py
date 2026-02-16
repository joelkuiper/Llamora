from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Tuple

from llamora.app.services.crypto import CryptoContext

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from llamora.app.api.search import SearchAPI
    from llamora.app.services.search_config import SearchConfig


logger = logging.getLogger(__name__)


Job = Tuple[CryptoContext, str, str]


DEFAULT_MAX_QUEUE_SIZE = 1024
DEFAULT_ENQUEUE_TIMEOUT = 5.0


class IndexWorker:
    """Background worker that indexes entries for search."""

    def __init__(
        self,
        search_api: SearchAPI,
        *,
        search_config: SearchConfig | None = None,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        enqueue_timeout: float | None = DEFAULT_ENQUEUE_TIMEOUT,
        batch_size: int = 1,
        flush_interval: float = 0.1,
    ) -> None:
        self._search_api = search_api
        self._queue: asyncio.Queue[Job | None] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task | None = None
        self._enqueue_timeout = enqueue_timeout
        self._backpressure_events = 0
        self._dropped_jobs = 0
        self._batch_size = max(batch_size, 1)
        self._flush_interval = max(flush_interval, 0.0)
        self._search_config = search_config

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

    async def enqueue(self, ctx: CryptoContext, entry_id: str, plaintext: str) -> None:
        """Queue an entry for indexing."""
        job_ctx = ctx.fork()
        try:
            self._queue.put_nowait((job_ctx, entry_id, plaintext))
        except asyncio.QueueFull:
            job = (job_ctx, entry_id, plaintext)
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
                job_ctx.drop()
                put_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await put_task

    async def _flush_batch(self, batch: list[Job]) -> None:
        if not batch:
            return
        start = time.perf_counter()
        try:
            await self._search_api.bulk_index(batch)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to index batch of %d entries", len(batch))
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Indexed %d entries in %.1fms (queue=%d, dropped=%d)",
                len(batch),
                elapsed_ms,
                self._queue.qsize(),
                self._dropped_jobs,
            )
        finally:
            for ctx, _, _ in batch:
                ctx.drop()
            for _ in batch:
                self._queue.task_done()

    async def _run(self) -> None:
        maxsize = self._queue.maxsize
        batch: list[Job] = []
        flush_interval = self._flush_interval if self._flush_interval > 0 else None
        while True:
            timeout = flush_interval if batch and flush_interval is not None else None
            timed_out = False
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                job = None
                timed_out = True

            if job is None:
                if batch:
                    await self._flush_batch(batch)
                    batch = []
                if not timed_out:
                    self._queue.task_done()
                    break
                continue

            batch.append(job)
            if len(batch) >= self._batch_size:
                await self._flush_batch(batch)
                batch = []
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

    @property
    def search_config(self) -> SearchConfig | None:
        """Return the search configuration associated with the worker."""

        return self._search_config
