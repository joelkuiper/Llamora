import asyncio
import logging
import time
from heapq import heappop, heappush
from itertools import count
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Callable

from llamora.llm.client import LLMClient

from llamora.app.services.chat_meta import ChatMetaParser

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
        pending_ttl: int,
        params: dict | None = None,
        context: dict | None = None,
        messages: list[dict[str, str]] | None = None,
        reply_to: str | None = None,
        meta_extra: dict | None = None,
    ) -> None:
        self.user_msg_id = user_msg_id
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
        self.reply_to = reply_to if reply_to is not None else user_msg_id
        self.meta_extra = meta_extra or {}
        self.cancelled = False
        self.created_at = time.monotonic()
        self.assistant_msg_id: str | None = None
        self._chunks: deque[str] = deque()
        self._total_len = 0
        self._cleanup = on_cleanup
        self._cleanup_called = False
        self._session = LLMStreamSession(
            llm, user_msg_id, history, params, context, messages
        )
        self._parser = ChatMetaParser()
        self._visible_total = ""
        self._pipeline = ResponsePipeline(
            session=self._session,
            parser=self._parser,
            writer=AssistantMessageWriter(db),
            uid=uid,
            reply_to=self.reply_to,
            date=self.date,
            dek=self.dek,
            meta_extra=self.meta_extra,
            timeout=pending_ttl,
        )
        logger.debug("Starting generation for user message %s", user_msg_id)
        self._task = asyncio.create_task(
            self._run_pipeline(), name=f"pending:{user_msg_id}"
        )
        self._task.add_done_callback(self._handle_task_result)

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
        await self._pipeline.request_cancel()
        await self._await_task_completion()

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

    def __init__(self, retry_after: float, message: str | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message or "Streaming capacity exhausted")


class ChatStreamManager:
    def __init__(
        self,
        llm: LLMClient,
        db=None,
        pending_ttl: int = 300,
    ) -> None:
        self._llm = llm
        self._db = db
        self._pending_ttl = pending_ttl
        self._pending: dict[str, PendingResponse] = {}
        self._pending_heap: list[tuple[float, int, str, PendingResponse]] = []
        self._heap_counter = count()

    def set_db(self, db) -> None:
        self._db = db

    def get(self, user_msg_id: str) -> PendingResponse | None:
        return self._pending.get(user_msg_id)

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
                self._remove_pending(user_msg_id)

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
            self._remove_pending(user_msg_id)

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
    ) -> PendingResponse:
        self._prune_stale_pending()
        pending = self._pending.get(user_msg_id)
        if pending:
            return pending

        if self._db is None:
            raise RuntimeError("ChatStreamManager database is not configured")

        max_slots = max(1, int(getattr(self._llm, "parallel_slots", 1)))
        active_pending = sum(1 for item in self._pending.values() if not item.done)
        if active_pending >= max_slots:
            raise StreamCapacityError(retry_after=1.0)

        pending = PendingResponse(
            user_msg_id,
            uid,
            date,
            history,
            dek,
            self._llm,
            self._db,
            self._remove_pending,
            self._pending_ttl,
            params,
            context,
            messages,
            reply_to,
            meta_extra,
        )
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
        return pending

    async def stop(self, user_msg_id: str) -> tuple[bool, bool]:
        self._prune_stale_pending()
        pending = self._pending.get(user_msg_id)
        if pending:
            logger.debug("Cancelling pending response %s", user_msg_id)
            await pending.cancel()
            return True, True
        handled = await self._llm.abort(user_msg_id)
        return handled, False

    def _remove_pending(self, user_msg_id: str) -> None:
        self._pending.pop(user_msg_id, None)

    async def shutdown(self) -> None:
        """Cancel all in-flight responses and await their completion."""

        self._prune_stale_pending()
        for pending in list(self._pending.values()):
            with suppress(Exception):
                await pending.cancel()
        self._pending.clear()
        self._pending_heap.clear()


__all__ = [
    "ChatStreamManager",
    "LLMStreamSession",
    "PendingResponse",
    "StreamCapacityError",
]
