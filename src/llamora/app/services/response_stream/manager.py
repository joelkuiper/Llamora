from __future__ import annotations

import asyncio
import logging
import time
from heapq import heappop, heappush
from itertools import count
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import suppress

from llamora.llm.client import LLMClient
from llamora.app.services.service_pulse import ServicePulse
from llamora.app.services.queues import FairAsyncQueue

from ..llm_stream_config import LLMStreamConfig

from .pipeline import (
    AssistantMessageWriter,
    LLMStreamError,
    PipelineResult,
    ResponsePipeline,
    ResponsePipelineCallbacks,
)


logger = logging.getLogger(__name__)


class LLMStreamSession:
    """Encapsulates an LLM streaming session for a single response."""

    def __init__(
        self,
        llm: LLMClient,
        user_msg_id: str,
        history: list[dict],
        params: dict | None,
        context: dict | None,
        messages: list[dict[str, str]] | None,
    ) -> None:
        self._llm = llm
        self.user_msg_id = user_msg_id
        self._history = history
        self._params = params
        self._context = context or {}
        self._messages = messages
        self._first_chunk = True

    async def __aiter__(self) -> AsyncIterator[str]:
        async for chunk in self._llm.stream_response(
            self.user_msg_id,
            self._history,
            self._params,
            self._context,
            messages=self._messages,
        ):
            if isinstance(chunk, dict) and chunk.get("type") == "error":
                logger.info("Error chunk received for %s: %s", self.user_msg_id, chunk)
                raise LLMStreamError(chunk.get("data", "Unknown error"))
            text = chunk
            if not isinstance(text, str):
                text = str(text)
            if self._first_chunk:
                text = text.lstrip()
                self._first_chunk = False
            yield text

    async def abort(self) -> None:
        await self._llm.abort(self.user_msg_id)


class PendingResponse(ResponsePipelineCallbacks):
    """Tracks an in-flight assistant reply to a user's message."""

    def __init__(
        self,
        user_msg_id: str,
        uid: str,
        date: str,
        history: list[dict],
        dek: bytes,
        llm: LLMClient,
        db,
        on_cleanup: Callable[[str], None],
        config: LLMStreamConfig,
        params: dict | None = None,
        context: dict | None = None,
        messages: list[dict[str, str]] | None = None,
        reply_to: str | None = None,
        meta_extra: dict | None = None,
        *,
        use_default_reply_to: bool = True,
        auto_start: bool = True,
    ) -> None:
        self.user_msg_id = user_msg_id
        self._uid = uid
        self.date = date
        self.text = ""
        self.done = False
        self.error = False
        self.error_message = ""
        self._cond = asyncio.Condition()
        self.dek = dek
        self.meta: dict | None = None
        self.context = context or {}
        self.messages = messages
        if use_default_reply_to and reply_to is None:
            self.reply_to = user_msg_id
        else:
            self.reply_to = reply_to
        self.meta_extra = meta_extra or {}
        self.cancelled = False
        self.created_at = time.monotonic()
        self.assistant_msg_id: str | None = None
        self._chunks: deque[str] = deque()
        self._total_len = 0
        self._cleanup = on_cleanup
        self._cleanup_called = False
        self._start_event = asyncio.Event()
        self._activated = False
        self.started_at: float | None = None
        self._session = LLMStreamSession(
            llm, user_msg_id, history, params, context, messages
        )
        self._visible_total = ""
        _repeat_guard_size = config.repeat_guard_size
        _repeat_guard_min_length = config.repeat_guard_min_length
        self._pipeline = ResponsePipeline(
            session=self._session,
            writer=AssistantMessageWriter(db),
            uid=uid,
            reply_to=self.reply_to,
            date=self.date,
            dek=self.dek,
            meta_extra=self.meta_extra,
            config=config,
        )
        logger.debug("Starting generation for user message %s", user_msg_id)
        self._task = asyncio.create_task(
            self._run_pipeline(), name=f"pending:{user_msg_id}"
        )
        self._task.add_done_callback(self._handle_task_result)
        if auto_start:
            self.start()

    @property
    def uid(self) -> str:
        return self._uid

    def _handle_task_result(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return

        exc = task.exception()
        if exc is None:
            return

        logger.exception(
            "Pending response pipeline task failed for %s",
            self.user_msg_id,
            exc_info=exc,
        )

    async def _run_pipeline(self) -> None:
        try:
            await self._start_event.wait()
            if self.cancelled and not self._activated:
                async with self._cond:
                    self.done = True
                    self._cond.notify_all()
                return
            await self._pipeline.run(self)
        finally:
            self._invoke_cleanup()

    async def on_visible(self, chunk: str, total: str) -> None:
        self._visible_total = total
        async with self._cond:
            if chunk:
                self._chunks.append(chunk)
            self._store_total_text(total)
            self._cond.notify_all()

    async def on_finished(self, result: PipelineResult) -> None:
        self.error = result.error
        self.error_message = result.error_message or ""
        self.cancelled = result.cancelled
        if result.assistant_message_id:
            self.assistant_msg_id = result.assistant_message_id
        async with self._cond:
            final_text = result.final_text
            if final_text:
                remaining = final_text[self._total_len :]
                if remaining:
                    self._chunks.append(remaining)
            self._store_total_text(final_text)
            self.meta = result.meta
            self.done = True
            self._cond.notify_all()

    def _invoke_cleanup(self) -> None:
        if not self._cleanup_called:
            self._cleanup_called = True
            try:
                self._cleanup(self.user_msg_id)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Cleanup callback failed for %s", self.user_msg_id)

    async def cancel(self) -> None:
        self.cancelled = True
        self._start_event.set()
        await self._pipeline.request_cancel()
        await self._await_task_completion()

    def start(self) -> bool:
        if self._activated:
            return False
        if self.cancelled:
            self._start_event.set()
            return False
        self._activated = True
        self.started_at = time.monotonic()
        self._start_event.set()
        return True

    @property
    def activated(self) -> bool:
        return self._activated

    async def _await_task_completion(self) -> None:
        """Wait for the generation task to finish persisting state.

        When a user stops a stream we want to persist whatever text was already
        generated.  ``PendingResponse`` normally finalises this work inside the
        background task stored in ``self._task``.  Previously we cancelled that
        task immediately which could prevent the cancellation finaliser from
        running, resulting in the partial response never being saved.  By
        allowing the task a brief grace period to wrap up we ensure the partial
        message is written to the database.  If the task is still running after
        the timeout we fall back to cancelling it to avoid hanging.
        """

        if self._task.done():
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            return

        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

    async def stream(self):
        while True:
            async with self._cond:
                while not self._chunks and not self.done:
                    await self._cond.wait()
                if self._chunks:
                    chunk = self._chunks.popleft()
                else:
                    if self.done:
                        break
                    continue
            yield chunk

    def _store_total_text(self, total: str) -> None:
        self.text = total
        self._total_len = len(total)
        self._visible_total = total


class StreamCapacityError(RuntimeError):
    """Raised when no parallel slots are available for a new stream."""

    def __init__(
        self, retry_after: float, queue_depth: int = 0, message: str | None = None
    ) -> None:
        self.retry_after = retry_after
        self.queue_depth = queue_depth
        super().__init__(message or "Streaming capacity exhausted")


class ResponseStreamManager:
    def __init__(
        self,
        llm: LLMClient,
        db=None,
        *,
        stream_config: LLMStreamConfig,
        service_pulse: ServicePulse | None = None,
    ) -> None:
        self._llm = llm
        self._db = db
        self._config = stream_config
        self._pending_ttl = stream_config.pending_ttl
        self._pending: dict[str, PendingResponse] = {}
        self._pending_heap: list[tuple[float, int, str, PendingResponse]] = []
        self._heap_counter = count()
        self._queue_limit = max(0, stream_config.queue_limit)
        self._queue = FairAsyncQueue[str, PendingResponse](
            id_getter=lambda pending: pending.user_msg_id
        )
        self._queue.add_listener(self._handle_queue_change)
        self._queue_hooks: set[Callable[[dict[str, int]], None]] = set()
        self._active_ids: set[str] = set()
        self._queue_consumer: asyncio.Task[None] | None = None
        self._queue_loop: asyncio.AbstractEventLoop | None = None
        self._slot_event: asyncio.Event | None = None
        self._service_pulse = service_pulse
        self._avg_stream_duration: float | None = None
        self._avg_queue_wait: float | None = None

    def set_db(self, db) -> None:
        self._db = db

    def get(self, user_msg_id: str, uid: str) -> PendingResponse | None:
        pending = self._pending.get(user_msg_id)
        if not pending:
            return None

        if pending.uid != uid:
            logger.warning(
                "UID mismatch for pending response %s (stored=%s, caller=%s)",
                user_msg_id,
                pending.uid,
                uid,
            )
            self._pending.pop(user_msg_id, None)
            self._schedule_pending_cancellation(user_msg_id, pending)
            return None

        return pending

    def _prune_stale_pending(self, now: float | None = None) -> None:
        if not self._pending_heap:
            return

        if now is None:
            now = time.monotonic()

        ttl = self._pending_ttl
        heap = self._pending_heap
        while heap:
            expiry, _, user_msg_id, pending = heap[0]
            if expiry > now:
                break

            heappop(heap)
            current = self._pending.get(user_msg_id)
            if current is not pending:
                continue

            actual_expiry = pending.created_at + ttl
            if actual_expiry > now:
                heappush(
                    heap,
                    (actual_expiry, next(self._heap_counter), user_msg_id, pending),
                )
                continue

            logger.debug("Dropping stale pending response %s", user_msg_id)
            self._schedule_pending_cancellation(user_msg_id, pending)

    def _schedule_pending_cancellation(
        self, user_msg_id: str, pending: "PendingResponse"
    ) -> None:
        async def _cancel_and_remove() -> None:
            try:
                await pending.cancel()
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "Failed to cancel stale pending response %s", user_msg_id
                )
            finally:
                self._on_pending_cleanup(user_msg_id)

        loop = pending._task.get_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(loop.create_task, _cancel_and_remove())
        elif not loop.is_closed():
            # If the loop isn't running we fall back to executing the cancellation
            # synchronously so cleanup still occurs.  This keeps the method safe to
            # call from synchronous contexts such as shutdown handlers.
            loop.run_until_complete(_cancel_and_remove())
        else:  # pragma: no cover - defensive
            logger.warning(
                "Event loop already closed while cancelling stale response %s",
                user_msg_id,
            )
            self._on_pending_cleanup(user_msg_id)

    def start_stream(
        self,
        user_msg_id: str,
        uid: str,
        date: str,
        history: list[dict],
        dek: bytes,
        params: dict | None = None,
        context: dict | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        reply_to: str | None = None,
        meta_extra: dict | None = None,
        use_default_reply_to: bool = True,
    ) -> PendingResponse:
        self._prune_stale_pending()
        self._ensure_queue_worker()
        pending = self._pending.get(user_msg_id)
        if pending:
            if pending.uid == uid:
                return pending

            logger.warning(
                "UID mismatch on start for %s (stored=%s, caller=%s)",
                user_msg_id,
                pending.uid,
                uid,
            )
            self._pending.pop(user_msg_id, None)
            self._schedule_pending_cancellation(user_msg_id, pending)

        if self._db is None:
            raise RuntimeError("ResponseStreamManager database is not configured")

        max_slots = self._max_slots()
        queue_depth = len(self._queue)

        if len(self._active_ids) >= max_slots:
            if self._queue_limit and queue_depth >= self._queue_limit:
                retry_after = self._estimate_retry_after(queue_depth + 1, max_slots)
                raise StreamCapacityError(retry_after, queue_depth=queue_depth)

            pending = PendingResponse(
                user_msg_id,
                uid,
                date,
                history,
                dek,
                self._llm,
                self._db,
                self._on_pending_cleanup,
                self._config,
                params,
                context,
                messages,
                reply_to,
                meta_extra,
                use_default_reply_to=use_default_reply_to,
                auto_start=False,
            )
            self._register_pending(pending)
            self._queue.enqueue(uid, pending)
            self._ensure_queue_worker()
            return pending

        pending = PendingResponse(
            user_msg_id,
            uid,
            date,
            history,
            dek,
            self._llm,
            self._db,
            self._on_pending_cleanup,
            self._config,
            params,
            context,
            messages,
            reply_to,
            meta_extra,
            use_default_reply_to=use_default_reply_to,
            auto_start=False,
        )
        self._register_pending(pending)
        self._activate_pending(pending)
        return pending

    async def stop(self, user_msg_id: str, uid: str) -> tuple[bool, bool]:
        self._prune_stale_pending()
        pending = self._pending.get(user_msg_id)
        if pending and pending.uid != uid:
            logger.warning(
                "UID mismatch on stop for %s (stored=%s, caller=%s)",
                user_msg_id,
                pending.uid,
                uid,
            )
            self._pending.pop(user_msg_id, None)
            self._schedule_pending_cancellation(user_msg_id, pending)
            pending = None

        if pending:
            if not pending.activated:
                self._queue.remove(user_msg_id)
            logger.debug("Cancelling pending response %s", user_msg_id)
            await pending.cancel()
            return True, True
        handled = await self._llm.abort(user_msg_id)
        return handled, False

    def _on_pending_cleanup(self, user_msg_id: str) -> None:
        pending = self._pending.pop(user_msg_id, None)
        if pending and pending.started_at is not None:
            duration = max(0.0, time.monotonic() - pending.started_at)
            self._update_stream_duration(duration)
        self._queue.remove(user_msg_id)
        self._active_ids.discard(user_msg_id)
        self._publish_queue_state()
        self._update_slot_event()
        self._ensure_queue_worker()

    def _register_pending(self, pending: PendingResponse) -> None:
        user_msg_id = pending.user_msg_id
        self._pending[user_msg_id] = pending
        heappush(
            self._pending_heap,
            (
                pending.created_at + self._pending_ttl,
                next(self._heap_counter),
                user_msg_id,
                pending,
            ),
        )

    def _activate_pending(self, pending: PendingResponse) -> None:
        if pending.start():
            self._active_ids.add(pending.user_msg_id)
            if pending.started_at is not None:
                wait_time = max(0.0, pending.started_at - pending.created_at)
                self._update_wait_estimate(wait_time)
        self._update_slot_event()
        self._publish_queue_state()

    def _max_slots(self) -> int:
        return max(1, int(getattr(self._llm, "parallel_slots", 1)))

    def add_queue_hook(self, callback: Callable[[dict[str, int]], None]) -> None:
        self._queue_hooks.add(callback)
        try:
            callback(self._build_queue_snapshot())
        except Exception:  # pragma: no cover - defensive
            logger.exception("Response stream queue hook failed")

    def remove_queue_hook(self, callback: Callable[[dict[str, int]], None]) -> None:
        self._queue_hooks.discard(callback)

    def _handle_queue_change(
        self, _queue: FairAsyncQueue[str, PendingResponse]
    ) -> None:
        self._publish_queue_state()
        self._ensure_queue_worker()

    def _build_queue_snapshot(self) -> dict[str, int]:
        return {
            "depth": len(self._queue),
            "active": len(self._active_ids),
            "limit": self._queue_limit,
            "slots": self._max_slots(),
        }

    def _publish_queue_state(self) -> None:
        snapshot = self._build_queue_snapshot()
        if self._service_pulse is not None:
            try:
                self._service_pulse.emit("response_stream.queue", snapshot)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Failed to emit response stream queue pulse")
        for hook in list(self._queue_hooks):
            try:
                hook(snapshot)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Response stream queue hook failed")

    def _ensure_queue_worker(self) -> None:
        if self._queue_consumer is not None and not self._queue_consumer.done():
            return
        loop = self._get_running_loop()
        if loop is None:
            return
        if self._queue_loop is None:
            self._queue_loop = loop
        if self._slot_event is None:
            self._slot_event = asyncio.Event()
        self._update_slot_event()
        task = loop.create_task(self._queue_consumer_loop())
        task.add_done_callback(self._on_queue_worker_done)
        self._queue_consumer = task

    async def _queue_consumer_loop(self) -> None:
        try:
            while True:
                await self._wait_for_slot()
                pending = await self._queue.async_pop()
                self._activate_pending(pending)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("Response stream queue worker failed")
            raise

    async def _wait_for_slot(self) -> None:
        while len(self._active_ids) >= self._max_slots():
            self._update_slot_event()
            event = self._slot_event
            if event is None:
                event = asyncio.Event()
                self._slot_event = event
                self._update_slot_event()
            await event.wait()

    def _update_slot_event(self) -> None:
        event = self._slot_event
        if event is None:
            return
        if len(self._active_ids) < self._max_slots():
            event.set()
        else:
            event.clear()

    def _on_queue_worker_done(self, task: asyncio.Task[None]) -> None:
        self._queue_consumer = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception("Response stream queue worker exited", exc_info=exc)
            if self._queue:
                self._ensure_queue_worker()

    def _get_running_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _update_stream_duration(self, sample: float) -> None:
        if sample <= 0:
            return
        alpha = 0.2
        if self._avg_stream_duration is None:
            self._avg_stream_duration = sample
        else:
            self._avg_stream_duration = (
                1 - alpha
            ) * self._avg_stream_duration + alpha * sample

    def _update_wait_estimate(self, sample: float) -> None:
        if sample < 0:
            return
        alpha = 0.2
        if self._avg_queue_wait is None:
            self._avg_queue_wait = sample
        else:
            self._avg_queue_wait = (1 - alpha) * self._avg_queue_wait + alpha * sample

    def _estimate_retry_after(self, queue_depth: int, max_slots: int) -> float:
        avg_wait = self._avg_queue_wait if self._avg_queue_wait is not None else 0.75
        avg_stream = (
            self._avg_stream_duration if self._avg_stream_duration is not None else 7.5
        )
        slots = max(1, max_slots)
        estimate = avg_wait + (queue_depth / slots) * max(avg_stream, 1.0)
        return max(1.0, min(30.0, estimate))

    async def shutdown(self) -> None:
        """Cancel all in-flight responses and await their completion."""

        self._prune_stale_pending()
        for pending in list(self._pending.values()):
            with suppress(Exception):
                await pending.cancel()
        self._pending.clear()
        self._pending_heap.clear()
        self._queue.clear()
        self._active_ids.clear()
        self._publish_queue_state()
        if self._queue_consumer is not None:
            self._queue_consumer.cancel()
            with suppress(Exception):
                await self._queue_consumer
            self._queue_consumer = None


__all__ = [
    "ResponseStreamManager",
    "LLMStreamSession",
    "PendingResponse",
    "StreamCapacityError",
]
