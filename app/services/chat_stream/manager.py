import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Callable

from llm.client import LLMClient

from app.services.chat_meta import ChatMetaParser

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
        prompt: str | None,
    ) -> None:
        self._llm = llm
        self.user_msg_id = user_msg_id
        self._history = history
        self._params = params
        self._context = context or {}
        self._prompt = prompt
        self._first_chunk = True

    async def __aiter__(self) -> AsyncIterator[str]:
        async for chunk in self._llm.stream_response(
            self.user_msg_id,
            self._history,
            self._params,
            self._context,
            prompt=self._prompt,
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
        prompt: str | None = None,
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
        self.prompt = prompt
        self.reply_to = reply_to if reply_to is not None else user_msg_id
        self.meta_extra = meta_extra or {}
        self.cancelled = False
        self.created_at = asyncio.get_event_loop().time()
        self.assistant_msg_id: str | None = None
        self._cleanup = on_cleanup
        self._cleanup_called = False
        self._session = LLMStreamSession(
            llm, user_msg_id, history, params, context, prompt
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
        self._task.add_done_callback(lambda _: self._invoke_cleanup())

    async def _run_pipeline(self) -> None:
        try:
            await self._pipeline.run(self)
        finally:
            self._invoke_cleanup()

    async def on_visible(self, chunk: str, total: str) -> None:
        self._visible_total = total
        async with self._cond:
            self.text = total
            self._cond.notify_all()

    async def on_finished(self, result: PipelineResult) -> None:
        self.error = result.error
        self.error_message = result.error_message or ""
        self.cancelled = result.cancelled
        if result.assistant_message_id:
            self.assistant_msg_id = result.assistant_message_id
        async with self._cond:
            self.text = result.final_text
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
        self._invoke_cleanup()

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
        sent = 0
        while True:
            async with self._cond:
                while len(self.text) == sent and not self.done:
                    await self._cond.wait()
                chunk = self.text[sent:]
                sent = len(self.text)
                if chunk:
                    yield chunk
                if self.done:
                    break


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

    def set_db(self, db) -> None:
        self._db = db

    def get(self, user_msg_id: str) -> PendingResponse | None:
        return self._pending.get(user_msg_id)

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
        prompt: str | None = None,
        reply_to: str | None = None,
        meta_extra: dict | None = None,
    ) -> PendingResponse:
        pending = self._pending.get(user_msg_id)
        if pending:
            return pending

        if self._db is None:
            raise RuntimeError("ChatStreamManager database is not configured")

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
            prompt,
            reply_to,
            meta_extra,
        )
        self._pending[user_msg_id] = pending
        return pending

    async def stop(self, user_msg_id: str) -> tuple[bool, bool]:
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

        for pending in list(self._pending.values()):
            with suppress(Exception):
                await pending.cancel()
        self._pending.clear()


__all__ = ["ChatStreamManager", "LLMStreamSession", "PendingResponse"]
