"""Streaming pipeline utilities for assistant responses."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


from ..llm_stream_config import LLMStreamConfig


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
        reply_to: str | None,
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


@dataclass(slots=True)
class ChunkRingGuard:
    """Detects repeated visible chunks and trailing patterns within a window."""

    size: int
    min_length: int = 0
    _ring: deque[str] | None = field(init=False, repr=False)
    _buffer: str = field(init=False, repr=False, default="")

    def __post_init__(self) -> None:
        self.size = max(int(self.size), 0)
        self.min_length = max(int(self.min_length), 0)
        self._ring = deque(maxlen=self.size) if self.size > 0 else None
        self._buffer = ""

    def record(self, chunk: str, *, total: str | None = None) -> bool:
        """Track a chunk and report if repetition heuristics are triggered."""

        if total is not None and self._detect_total_repeat(total):
            self._reset()
            return True

        if self._ring is None:
            return False

        normalised = self._normalise(chunk)
        if not normalised:
            self._reset()
            return False

        candidate = normalised
        if self.min_length > 0:
            candidate = self._buffer + candidate
            if len(candidate) < self.min_length:
                self._buffer = candidate
                return False
            self._buffer = ""

        self._ring.append(candidate)
        maxlen = self._ring.maxlen
        if maxlen is None or len(self._ring) < maxlen:
            return False

        first = self._ring[0]
        return bool(first) and all(entry == first for entry in self._ring)

    def _reset(self) -> None:
        self._buffer = ""
        if self._ring is not None:
            self._ring.clear()

    @staticmethod
    def _normalise(chunk: str) -> str:
        return " ".join(chunk.split())

    def _detect_total_repeat(self, total: str) -> bool:
        if self.size < 2:
            return False

        normalised_total = self._normalise(total)
        if not normalised_total:
            return False

        tokens = normalised_total.split(" ")
        if len(tokens) < self.size:
            return False

        max_tail_tokens = min(len(tokens), max(self.size * 64, 256))
        tail_tokens = tokens[-max_tail_tokens:]
        max_pattern_tokens = len(tail_tokens) // self.size
        if max_pattern_tokens == 0:
            return False

        for length in range(1, max_pattern_tokens + 1):
            pattern_tokens = tail_tokens[-length:]
            if self.min_length:
                pattern_text = " ".join(pattern_tokens)
                if len(pattern_text) < self.min_length:
                    continue

            repeated = True
            for repeat_index in range(2, self.size + 1):
                start = -length * repeat_index
                end = None if start == 0 else -length * (repeat_index - 1)
                if tail_tokens[start:end] != pattern_tokens:
                    repeated = False
                    break

            if repeated:
                return True

        return False


class ResponsePipeline:
    """Coordinates the LLM streaming lifecycle."""

    def __init__(
        self,
        *,
        session,
        writer: AssistantMessageWriter,
        uid: str,
        reply_to: str | None,
        date: str,
        dek: bytes,
        meta_extra: dict | None = None,
        config: LLMStreamConfig,
    ) -> None:
        self._session = session
        self._writer = writer
        self._uid = uid
        self._reply_to = reply_to
        self._date = date
        self._dek = dek
        self._meta_extra = meta_extra or {}
        self._config = config
        self._timeout = config.pending_ttl
        self._visible_total = ""
        self._cancel_requested = False
        self._cancelled = False
        self._error = False
        self._error_message: str | None = None
        self._status_prefix = "⚠️ "
        self._repeat_guard_triggered = False
        guard_size = config.repeat_guard_size or 0
        guard_min_length = config.repeat_guard_min_length or 0
        self._chunk_guard = (
            ChunkRingGuard(guard_size, guard_min_length) if guard_size > 0 else None
        )

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
                self._append_status_line(
                    "", self._error_message, prefix=self._status_prefix
                ),
                error_meta=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Error during LLM streaming")
            self._error = True
            self._error_message = str(exc) or "An unexpected error occurred."
            result = await self._finalize_with_text(
                self._append_status_line(
                    self._visible_total,
                    self._error_message,
                    prefix=self._status_prefix,
                ),
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
                text = chunk if isinstance(chunk, str) else str(chunk)
                if not text:
                    continue
                candidate_total = full_response + text
                if self._chunk_guard and self._chunk_guard.record(
                    text, total=candidate_total
                ):
                    self._repeat_guard_triggered = True
                    if not self._cancel_requested:
                        self._cancel_requested = True
                    await self._abort_session()
                    break
                full_response = candidate_total
                self._visible_total = full_response
                await callbacks.on_visible(text, full_response)
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

    async def _build_metadata(
        self,
        text: str,
        *,
        error: bool,
        partial: bool,
        include_repeat_guard: bool,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {}

        if include_repeat_guard:
            meta["repeat_guard"] = True
        if error:
            meta["error"] = True
        if partial:
            meta["partial"] = True
        if self._meta_extra:
            meta.update(self._meta_extra)

        return meta

    async def _finalize_success(self, full_response: str) -> PipelineResult:
        final_text = full_response
        meta = await self._build_metadata(
            final_text,
            error=self._error,
            partial=False,
            include_repeat_guard=self._repeat_guard_triggered,
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
            error=self._error,
            error_message=self._error_message,
            cancelled=self._cancelled,
            partial=False,
        )

    async def _finalize_cancelled(self, *, partial: bool) -> PipelineResult:
        final_text = self._visible_total
        if self._error_message:
            final_text = self._append_status_line(
                final_text, self._error_message, prefix=self._status_prefix
            )
        meta = await self._build_metadata(
            final_text,
            error=self._error,
            partial=True,
            include_repeat_guard=self._repeat_guard_triggered,
        )
        assistant_msg_id, failed = await self._persist(final_text, meta, partial=True)
        if failed:
            final_text = self._append_persistence_warning(final_text)
            self._error = True
        return PipelineResult(
            final_text=final_text,
            meta=meta,
            assistant_message_id=assistant_msg_id,
            error=self._error,
            error_message=self._error_message,
            cancelled=True,
            partial=partial,
        )

    async def _finalize_with_text(
        self, final_text: str, *, error_meta: bool
    ) -> PipelineResult:
        meta = await self._build_metadata(
            final_text,
            error=error_meta or self._error,
            partial=False,
            include_repeat_guard=False,
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
        return ResponsePipeline._append_status_line(text, "Failed to save response.")

    @staticmethod
    def _append_status_line(
        text: str, message: str, *, prefix: str | None = None
    ) -> str:
        if not message:
            return text
        prefix = "⚠️ " if prefix is None else prefix
        status_message = message
        if text:
            separator = "\n\n" if not text.endswith("\n") else "\n"
        else:
            separator = ""
        return f"{text}{separator}{prefix}{status_message}"


__all__ = [
    "AssistantMessagePersistenceError",
    "AssistantMessageWriter",
    "ChunkRingGuard",
    "LLMStreamError",
    "PipelineResult",
    "ResponsePipeline",
    "ResponsePipelineCallbacks",
]
