import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from html import escape

import orjson

from llm.client import LLMClient


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


class MetaExtractor:
    """Extracts assistant-visible text and metadata from streamed chunks."""

    def __init__(self, sentinel_start: str = "<meta>", sentinel_end: str = "</meta>") -> None:
        self._start = sentinel_start
        self._end = sentinel_end
        self._found_start = False
        self._meta_complete = False
        self._meta_buffer = ""
        self._tail = ""

    def process(self, chunk: str) -> str:
        """Process a chunk and return the visible text portion."""

        data = self._tail + chunk
        self._tail = ""
        visible = ""

        if not self._found_start:
            idx = data.find(self._start)
            if idx != -1:
                visible = data[:idx]
                data = data[idx + len(self._start) :]
                self._found_start = True
                self._meta_buffer += data
            else:
                keep = len(data) - len(self._start) + 1
                if keep > 0:
                    visible = data[:keep]
                    self._tail = data[keep:]
                    return visible
                self._tail = data
                return ""
        else:
            self._meta_buffer += data
        if not self._meta_complete:
            end_idx = self._meta_buffer.find(self._end)
            if end_idx != -1:
                trailing = self._meta_buffer[end_idx + len(self._end) :]
                if trailing.strip():
                    logger.debug(
                        "Unexpected trailing content after </meta>: %r", trailing
                    )
                self._meta_buffer = self._meta_buffer[:end_idx]
                self._meta_complete = True
        return visible

    @property
    def found_start(self) -> bool:
        return self._found_start

    @property
    def meta_complete(self) -> bool:
        return self._meta_complete

    @property
    def meta_payload(self) -> str:
        return self._meta_buffer.strip()

    def flush_visible_tail(self) -> str:
        if self._found_start:
            return ""
        remainder = self._tail
        self._tail = ""
        return remainder


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
            raise AssistantMessagePersistenceError("Database does not support append_message")
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
            raise AssistantMessagePersistenceError(str(exc) or "Failed to save response") from exc


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
        self._session = LLMStreamSession(llm, user_msg_id, history, params, context, prompt)
        self._writer = AssistantMessageWriter(db)
        logger.debug("Starting generation for user message %s", user_msg_id)
        self._task = asyncio.create_task(
            self._generate(uid), name=f"pending:{user_msg_id}"
        )

    async def _generate(self, uid: str) -> None:
        full_response = ""
        extractor = MetaExtractor()
        try:
            async for chunk in self._session:
                visible = extractor.process(chunk)
                if visible:
                    full_response += visible
                    async with self._cond:
                        self.text = full_response
                        self._cond.notify_all()
                if extractor.meta_complete:
                    break
        except asyncio.CancelledError:
            self.cancelled = True
            return
        except LLMStreamError as exc:
            self.error = True
            self.error_message = str(exc) or "Unknown error"
            full_response = f"<span class='error'>{escape(self.error_message)}</span>"
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Error during LLM streaming")
            self.error = True
            self.error_message = str(exc) or "An unexpected error occurred."
            full_response += f"<span class='error'>{escape(self.error_message)}</span>"
        finally:
            await self._finalize(uid, full_response, extractor)

    async def _finalize(
        self,
        uid: str,
        full_response: str,
        extractor: MetaExtractor,
    ) -> None:
        full_response = full_response + extractor.flush_visible_tail()
        if self.cancelled:
            await self._handle_cancellation(uid, full_response)
            return

        meta = self._build_meta(extractor)
        if full_response.strip():
            try:
                assistant_msg_id = await self._writer.save(
                    uid,
                    full_response,
                    self.dek,
                    meta,
                    self.reply_to,
                    self.date,
                )
                self.assistant_msg_id = assistant_msg_id
                logger.debug("Saved assistant message %s", assistant_msg_id)
            except AssistantMessagePersistenceError:
                logger.exception("Failed to save assistant message")
                full_response += "<span class='error'>⚠️ Failed to save response.</span>"
                self.error = True
        async with self._cond:
            self.text = full_response
            self.meta = meta
            self.done = True
            self._cond.notify_all()
        self._invoke_cleanup()

    async def _handle_cancellation(self, uid: str, full_response: str) -> None:
        if full_response.strip():
            meta: dict = {"error": True} if self.error else {}
            if self.meta_extra:
                meta.update(self.meta_extra)
            try:
                assistant_msg_id = await self._writer.save(
                    uid,
                    full_response,
                    self.dek,
                    meta,
                    self.reply_to,
                    self.date,
                )
                self.assistant_msg_id = assistant_msg_id
                logger.debug("Saved partial assistant message %s", assistant_msg_id)
            except AssistantMessagePersistenceError:
                logger.exception("Failed to save partial assistant message")
                full_response += "<span class='error'>⚠️ Failed to save response.</span>"
                self.error = True
        async with self._cond:
            self.text = full_response
            self.meta = None
            self.done = True
            self._cond.notify_all()
        self._invoke_cleanup()

    def _build_meta(self, extractor: MetaExtractor) -> dict:
        if extractor.found_start and extractor.meta_payload:
            try:
                meta = orjson.loads(extractor.meta_payload)
            except Exception:
                meta = {}
        else:
            meta = {}
        if self.error:
            meta["error"] = True
        if self.meta_extra:
            meta.update(self.meta_extra)
        return meta

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
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._invoke_cleanup()

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
        db,
        pending_ttl: int = 300,
        cleanup_interval: int = 60,
    ) -> None:
        self._llm = llm
        self._db = db
        self._pending_ttl = pending_ttl
        self._cleanup_interval = cleanup_interval
        self._pending: dict[str, PendingResponse] = {}
        self._cleanup_task: asyncio.Task | None = None

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

        pending = PendingResponse(
            user_msg_id,
            uid,
            date,
            history,
            dek,
            self._llm,
            self._db,
            self._remove_pending,
            params,
            context,
            prompt,
            reply_to,
            meta_extra,
        )
        self._pending[user_msg_id] = pending
        self._ensure_cleanup_task()
        return pending

    async def stop(self, user_msg_id: str) -> tuple[bool, bool]:
        pending = self._pending.get(user_msg_id)
        if pending:
            logger.debug("Cancelling pending response %s", user_msg_id)
            await pending.cancel()
            return True, True
        handled = await self._llm.abort(user_msg_id)
        return handled, False

    def _ensure_cleanup_task(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_expired())

    async def _cleanup_expired(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._cleanup_interval)
                cutoff = asyncio.get_event_loop().time() - self._pending_ttl
                for user_msg_id, pending in list(self._pending.items()):
                    if pending.done or pending.created_at < cutoff:
                        self._remove_pending(user_msg_id)
        except asyncio.CancelledError:
            pass

    def _remove_pending(self, user_msg_id: str) -> None:
        self._pending.pop(user_msg_id, None)


__all__ = [
    "LLMStreamError",
    "AssistantMessagePersistenceError",
    "LLMStreamSession",
    "MetaExtractor",
    "AssistantMessageWriter",
    "PendingResponse",
    "ChatStreamManager",
]
