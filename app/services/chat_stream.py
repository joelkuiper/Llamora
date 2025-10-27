import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from html import escape

from llm.client import LLMClient

from app.services.chat_meta import ChatMetaParser, build_meta


logger = logging.getLogger(__name__)


class LLMStreamError(Exception):
    """Raised when the LLM reports an error while streaming."""


class AssistantMessagePersistenceError(Exception):
    """Raised when the assistant response cannot be persisted."""


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


class AssistantMessageWriter:
    """Handles persistence of assistant responses."""

    def __init__(self, db) -> None:
        self._db = db

    async def save(
        self,
        uid: str,
        content: str,
        dek: bytes,
        meta: dict,
        reply_to: str,
        date: str,
    ) -> str:
        append = getattr(self._db, "append_message", None)
        if append is None and hasattr(self._db, "messages"):
            append = getattr(self._db.messages, "append_message", None)
        if append is None:
            raise AssistantMessagePersistenceError(
                "Database does not support append_message"
            )
        try:
            return await append(
                uid,
                "assistant",
                content,
                dek,
                meta,
                reply_to=reply_to,
                created_date=date,
            )
        except Exception as exc:  # pragma: no cover - defensive
            raise AssistantMessagePersistenceError(
                str(exc) or "Failed to save response"
            ) from exc


class ResponseStreamWorker:
    """Consumes an LLM session and forwards visible text to a callback."""

    def __init__(
        self,
        session: LLMStreamSession,
        parser: ChatMetaParser,
        on_visible: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._session = session
        self._parser = parser
        self._on_visible = on_visible

    async def run(self) -> str:
        """Stream chunks until completion and return the accumulated text."""

        full_response = ""
        async for chunk in self._session:
            visible = self._parser.feed(chunk)
            if visible:
                full_response += visible
                await self._on_visible(visible, full_response)
            if self._parser.meta_complete:
                break
        return full_response


class AssistantResponsePersister:
    """Persists assistant responses with consistent logging."""

    def __init__(
        self,
        writer: AssistantMessageWriter,
        uid: str,
        reply_to: str,
        date: str,
    ) -> None:
        self._writer = writer
        self._uid = uid
        self._reply_to = reply_to
        self._date = date

    async def persist(
        self,
        content: str,
        dek: bytes,
        meta: dict | None,
        *,
        partial: bool = False,
    ) -> tuple[str | None, bool]:
        if not content.strip():
            return None, False

        label = "partial assistant message" if partial else "assistant message"
        try:
            message_id = await self._writer.save(
                self._uid,
                content,
                dek,
                meta or {},
                self._reply_to,
                self._date,
            )
        except AssistantMessagePersistenceError:
            logger.exception("Failed to save %s", label)
            return None, True

        logger.debug("Saved %s %s", label, message_id)
        return message_id, False


class PendingResponse:
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
        self._writer = AssistantMessageWriter(db)
        self._parser = ChatMetaParser()
        self._visible_total = ""
        self._pending_ttl = pending_ttl
        logger.debug("Starting generation for user message %s", user_msg_id)
        self._task = asyncio.create_task(
            self._generate(uid), name=f"pending:{user_msg_id}"
        )
        self._task.add_done_callback(lambda _: self._invoke_cleanup())

    async def _generate(self, uid: str) -> None:
        worker = ResponseStreamWorker(
            self._session, self._parser, self._on_visible_chunk
        )
        persister = AssistantResponsePersister(
            self._writer, uid, self.reply_to, self.date
        )
        async with asyncio.TaskGroup() as tg:
            stream_task = tg.create_task(
                self._run_stream(worker), name=f"stream:{self.user_msg_id}"
            )
            tg.create_task(
                self._finalize_from_stream(persister, stream_task),
                name=f"finalize:{self.user_msg_id}",
            )

    async def _run_stream(self, worker: ResponseStreamWorker) -> str:
        if self._pending_ttl and self._pending_ttl > 0:
            timeout_ctx = getattr(asyncio, "timeout", None)
            if timeout_ctx is not None:
                async with timeout_ctx(self._pending_ttl):
                    return await worker.run()
            return await asyncio.wait_for(worker.run(), self._pending_ttl)
        return await worker.run()

    async def _on_visible_chunk(self, chunk: str, total: str) -> None:
        self._visible_total = total
        async with self._cond:
            self.text = total
            self._cond.notify_all()

    async def _finalize_from_stream(
        self,
        persister: AssistantResponsePersister,
        stream_task: asyncio.Task[str],
    ) -> None:
        try:
            full_response = await stream_task
        except asyncio.CancelledError:
            self.cancelled = True
            await self._finalize_cancelled(persister)
            return
        except asyncio.TimeoutError:
            logger.warning("Streaming timed out for %s", self.user_msg_id)
            self.cancelled = True
            self.error = True
            self.error_message = "The response took too long and was cancelled."
            with suppress(Exception):
                await self._session.abort()
            await self._finalize_cancelled(persister)
            return
        except LLMStreamError as exc:
            self.error = True
            self.error_message = str(exc) or "Unknown error"
            await self._finalize_with_text(
                persister,
                f"<span class='error'>{escape(self.error_message)}</span>",
                error_meta=True,
            )
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Error during LLM streaming")
            self.error = True
            self.error_message = str(exc) or "An unexpected error occurred."
            await self._finalize_with_text(
                persister,
                self._visible_total
                + f"<span class='error'>{escape(self.error_message)}</span>",
                error_meta=True,
            )
            return

        if self.cancelled:
            await self._finalize_cancelled(persister)
            return

        await self._finalize_success(persister, full_response)

    async def _finalize_success(
        self,
        persister: AssistantResponsePersister,
        full_response: str,
    ) -> None:
        final_text = full_response + self._parser.flush_visible_tail()
        meta = build_meta(self._parser, meta_extra=self.meta_extra, error=self.error)
        assistant_msg_id, failed = await persister.persist(
            final_text, self.dek, meta
        )
        if assistant_msg_id:
            self.assistant_msg_id = assistant_msg_id
        if failed:
            final_text = self._append_persistence_warning(final_text)
            self.error = True
            meta = build_meta(self._parser, meta_extra=self.meta_extra, error=True)
        await self._complete(final_text, meta)

    async def _finalize_cancelled(
        self, persister: AssistantResponsePersister
    ) -> None:
        final_text = self._visible_total + self._parser.flush_visible_tail()
        if self.error_message:
            final_text += f"<span class='error'>{escape(self.error_message)}</span>"
        meta: dict = {"error": True} if self.error else {}
        if self.meta_extra:
            meta.update(self.meta_extra)
        assistant_msg_id, failed = await persister.persist(
            final_text, self.dek, meta, partial=True
        )
        if assistant_msg_id:
            self.assistant_msg_id = assistant_msg_id
        if failed:
            final_text = self._append_persistence_warning(final_text)
            self.error = True
        await self._complete(final_text, None)

    async def _finalize_with_text(
        self,
        persister: AssistantResponsePersister,
        final_text: str,
        *,
        error_meta: bool,
    ) -> None:
        meta = build_meta(
            self._parser,
            meta_extra=self.meta_extra,
            error=error_meta or self.error,
        )
        assistant_msg_id, failed = await persister.persist(
            final_text, self.dek, meta
        )
        if assistant_msg_id:
            self.assistant_msg_id = assistant_msg_id
        if failed:
            final_text = self._append_persistence_warning(final_text)
            self.error = True
            meta["error"] = True
        await self._complete(final_text, meta)

    @staticmethod
    def _append_persistence_warning(text: str) -> str:
        return text + "<span class='error'>⚠️ Failed to save response.</span>"

    async def _complete(self, final_text: str, meta: dict | None) -> None:
        async with self._cond:
            self.text = final_text
            self.meta = meta
            self.done = True
            self._cond.notify_all()
        self._invoke_cleanup()

    def _invoke_cleanup(self) -> None:
        if not self._cleanup_called:
            self._cleanup_called = True
            try:
                self._cleanup(self.user_msg_id)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Cleanup callback failed for %s", self.user_msg_id)

    async def cancel(self) -> None:
        self.cancelled = True
        await self._session.abort()
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


__all__ = [
    "LLMStreamError",
    "AssistantMessagePersistenceError",
    "LLMStreamSession",
    "AssistantMessageWriter",
    "ResponseStreamWorker",
    "AssistantResponsePersister",
    "PendingResponse",
    "ChatStreamManager",
]
