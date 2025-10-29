"""Streaming pipeline utilities for assistant responses."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html import escape
from typing import Awaitable, Callable, Protocol

from app.services.chat_meta import ChatMetaParser, build_meta


logger = logging.getLogger(__name__)


class LLMStreamError(Exception):
    """Raised when the LLM reports an error while streaming."""


class AssistantMessagePersistenceError(Exception):
    """Raised when the assistant response cannot be persisted."""


class ResponsePipelineCallbacks(Protocol):
    """Callbacks invoked as the pipeline progresses."""

    async def on_visible(
        self, chunk: str, total: str
    ) -> None:  # pragma: no cover - interface
        ...

    async def on_finished(
        self, result: "PipelineResult"
    ) -> None:  # pragma: no cover - interface
        ...


@dataclass(slots=True)
class PipelineResult:
    """Outcome of a pipeline run."""

    final_text: str
    meta: dict | None
    assistant_message_id: str | None
    error: bool
    error_message: str | None
    cancelled: bool
    partial: bool


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
        append: Callable[..., Awaitable[str]] | None = getattr(
            self._db, "append_message", None
        )
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


class ResponsePipeline:
    """Coordinates the LLM streaming lifecycle."""

    def __init__(
        self,
        *,
        session,
        parser: ChatMetaParser,
        writer: AssistantMessageWriter,
        uid: str,
        reply_to: str,
        date: str,
        dek: bytes,
        meta_extra: dict | None = None,
        timeout: int | None = None,
    ) -> None:
        self._session = session
        self._parser = parser
        self._writer = writer
        self._uid = uid
        self._reply_to = reply_to
        self._date = date
        self._dek = dek
        self._meta_extra = meta_extra or {}
        self._timeout = timeout
        self._visible_total = ""
        self._cancel_requested = False
        self._cancelled = False
        self._error = False
        self._error_message: str | None = None

    async def run(self, callbacks: ResponsePipelineCallbacks) -> PipelineResult:
        """Execute the pipeline and notify callbacks."""

        try:
            full_response = await self._stream(callbacks)
        except asyncio.CancelledError:
            self._cancelled = True
            result = await self._finalize_cancelled(partial=True)
        except asyncio.TimeoutError:
            logger.warning(
                "Streaming timed out for %s",
                getattr(self._session, "user_msg_id", "<unknown>"),
            )
            self._cancelled = True
            self._error = True
            self._error_message = "The response took too long and was cancelled."
            await self._abort_session()
            result = await self._finalize_cancelled(partial=True)
        except LLMStreamError as exc:
            self._error = True
            self._error_message = str(exc) or "Unknown error"
            result = await self._finalize_with_text(
                f"<span class='error'>{escape(self._error_message)}</span>",
                error_meta=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Error during LLM streaming")
            self._error = True
            self._error_message = str(exc) or "An unexpected error occurred."
            result = await self._finalize_with_text(
                self._visible_total
                + f"<span class='error'>{escape(self._error_message)}</span>",
                error_meta=True,
            )
        else:
            if self._cancel_requested:
                self._cancelled = True
                result = await self._finalize_cancelled(partial=True)
            else:
                result = await self._finalize_success(full_response)

        await callbacks.on_finished(result)
        return result

    async def request_cancel(self) -> None:
        """Signal that the pipeline should cancel and abort the session."""

        if self._cancel_requested:
            return
        self._cancel_requested = True
        await self._abort_session()

    async def _abort_session(self) -> None:
        abort = getattr(self._session, "abort", None)
        if abort is None:
            return
        try:
            await abort()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to abort LLM session")

    async def _stream(self, callbacks: ResponsePipelineCallbacks) -> str:
        """Fetch and parse streamed chunks, notifying visibility updates."""

        async def consume() -> str:
            full_response = ""
            async for chunk in self._fetch_chunks():
                visible = self._parse_chunk(chunk)
                if visible:
                    full_response += visible
                    self._visible_total = full_response
                    await callbacks.on_visible(visible, full_response)
                if self._cancel_requested:
                    break
            return full_response

        if self._timeout and self._timeout > 0:
            timeout_ctx = getattr(asyncio, "timeout", None)
            if timeout_ctx is not None:
                async with timeout_ctx(self._timeout):
                    return await consume()
            return await asyncio.wait_for(consume(), self._timeout)
        return await consume()

    async def _fetch_chunks(self):
        async for chunk in self._session:
            if self._cancel_requested:
                break
            yield chunk

    def _parse_chunk(self, chunk: str) -> str:
        text = chunk if isinstance(chunk, str) else str(chunk)
        return self._parser.feed(text)

    async def _persist(
        self,
        content: str,
        meta: dict,
        *,
        partial: bool,
    ) -> tuple[str | None, bool]:
        if not content.strip():
            return None, False
        label = "partial assistant message" if partial else "assistant message"
        try:
            message_id = await self._writer.save(
                self._uid,
                content,
                self._dek,
                meta,
                self._reply_to,
                self._date,
            )
        except AssistantMessagePersistenceError:
            logger.exception("Failed to save %s", label)
            return None, True
        logger.debug("Saved %s %s", label, message_id)
        return message_id, False

    async def _finalize_success(self, full_response: str) -> PipelineResult:
        final_text = full_response + self._parser.flush_visible_tail()
        meta = build_meta(self._parser, meta_extra=self._meta_extra, error=self._error)
        assistant_msg_id, failed = await self._persist(final_text, meta, partial=False)
        if failed:
            final_text = self._append_persistence_warning(final_text)
            self._error = True
            meta = build_meta(self._parser, meta_extra=self._meta_extra, error=True)
        return PipelineResult(
            final_text=final_text,
            meta=meta,
            assistant_message_id=assistant_msg_id,
            error=self._error,
            error_message=self._error_message,
            cancelled=self._cancelled,
            partial=False,
        )

    async def _finalize_cancelled(self, *, partial: bool) -> PipelineResult:
        final_text = self._visible_total + self._parser.flush_visible_tail()
        if self._error_message:
            final_text += f"<span class='error'>{escape(self._error_message)}</span>"
        meta: dict = {"error": True} if self._error else {}
        if self._meta_extra:
            meta.update(self._meta_extra)
        assistant_msg_id, failed = await self._persist(final_text, meta, partial=True)
        if failed:
            final_text = self._append_persistence_warning(final_text)
            self._error = True
        return PipelineResult(
            final_text=final_text,
            meta=None,
            assistant_message_id=assistant_msg_id,
            error=self._error,
            error_message=self._error_message,
            cancelled=True,
            partial=partial,
        )

    async def _finalize_with_text(
        self, final_text: str, *, error_meta: bool
    ) -> PipelineResult:
        meta = build_meta(
            self._parser,
            meta_extra=self._meta_extra,
            error=error_meta or self._error,
        )
        assistant_msg_id, failed = await self._persist(final_text, meta, partial=False)
        if failed:
            final_text = self._append_persistence_warning(final_text)
            self._error = True
            meta["error"] = True
        return PipelineResult(
            final_text=final_text,
            meta=meta,
            assistant_message_id=assistant_msg_id,
            error=True,
            error_message=self._error_message,
            cancelled=self._cancelled,
            partial=False,
        )

    @staticmethod
    def _append_persistence_warning(text: str) -> str:
        return text + "<span class='error'>⚠️ Failed to save response.</span>"


__all__ = [
    "AssistantMessagePersistenceError",
    "AssistantMessageWriter",
    "LLMStreamError",
    "PipelineResult",
    "ResponsePipeline",
    "ResponsePipelineCallbacks",
]
