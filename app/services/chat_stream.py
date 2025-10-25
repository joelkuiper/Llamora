import asyncio
import logging
from collections.abc import Callable
from html import escape

import orjson

from llm.llm_engine import LLMEngine


logger = logging.getLogger(__name__)


class PendingResponse:
    """Tracks an in-flight assistant reply to a user's message."""

    def __init__(
        self,
        user_msg_id: str,
        uid: str,
        date: str,
        history: list[dict],
        dek: bytes,
        llm: LLMEngine,
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
        self._llm = llm
        self._db = db
        logger.debug("Starting generation for user message %s", user_msg_id)
        self._task = asyncio.create_task(
            self._generate(uid, history, params), name=f"pending:{user_msg_id}"
        )

    async def _generate(
        self,
        uid: str,
        history: list[dict],
        params: dict | None,
    ) -> None:
        full_response = ""
        sentinel_start = "<meta>"
        sentinel_end = "</meta>"
        tail = ""
        meta_buf = ""
        meta: dict | None = None
        found_start = False
        meta_complete = False
        first = True
        try:
            async for chunk in self._llm.stream_response(
                self.user_msg_id,
                history,
                params,
                self.context,
                prompt=self.prompt,
            ):
                if isinstance(chunk, dict) and chunk.get("type") == "error":
                    self.error = True
                    self.error_message = chunk.get("data", "Unknown error")
                    full_response = (
                        f"<span class='error'>{escape(self.error_message)}</span>"
                    )
                    logger.info("Error %s", chunk)
                    break

                if first:
                    chunk = chunk.lstrip()
                    first = False

                data = tail + chunk
                tail = ""

                if not found_start:
                    idx = data.find(sentinel_start)
                    if idx != -1:
                        vis = data[:idx]
                        if vis:
                            full_response += vis
                            async with self._cond:
                                self.text = full_response
                                self._cond.notify_all()
                        data = data[idx + len(sentinel_start) :]
                        found_start = True
                        meta_buf += data
                    else:
                        keep = len(data) - len(sentinel_start) + 1
                        if keep > 0:
                            vis = data[:keep]
                            full_response += vis
                            async with self._cond:
                                self.text = full_response
                                self._cond.notify_all()
                            tail = data[keep:]
                        else:
                            tail = data
                        continue
                else:
                    meta_buf += data

                if found_start and not meta_complete:
                    end_idx = meta_buf.find(sentinel_end)
                    if end_idx != -1:
                        trailing = meta_buf[end_idx + len(sentinel_end) :]
                        if trailing.strip():
                            logger.debug(
                                "Unexpected trailing content after </meta>: %r",
                                trailing,
                            )
                        meta_buf = meta_buf[:end_idx]
                        meta_complete = True
                        break

        except asyncio.CancelledError:
            self.cancelled = True
            return
        except Exception as exc:
            logger.exception("Error during LLM streaming")
            self.error = True
            self.error_message = str(exc) or "An unexpected error occurred."
            full_response += f"<span class='error'>{escape(self.error_message)}</span>"
        finally:
            await self._finalize(uid, full_response, meta_buf, found_start)

    async def _finalize(
        self,
        uid: str,
        full_response: str,
        meta_buf: str,
        found_start: bool,
    ) -> None:
        if self.cancelled:
            if full_response.strip():
                meta = {"error": True} if self.error else {}
                meta.update(self.meta_extra)
                try:
                    assistant_msg_id = await self._db.messages.append_message(
                        uid,
                        "assistant",
                        full_response,
                        self.dek,
                        meta,
                        reply_to=self.reply_to,
                        created_date=self.date,
                    )
                    self.assistant_msg_id = assistant_msg_id
                    logger.debug(
                        "Saved partial assistant message %s", assistant_msg_id
                    )
                except Exception:
                    logger.exception("Failed to save partial assistant message")
                    full_response += (
                        "<span class='error'>⚠️ Failed to save response.</span>"
                    )
                    self.error = True
            async with self._cond:
                self.text = full_response
                self.meta = None
                self.done = True
                self._cond.notify_all()
            self._invoke_cleanup()
            return

        meta_str = meta_buf.strip()
        if found_start and meta_str:
            try:
                meta = orjson.loads(meta_str)
            except Exception:
                meta = {}
        else:
            meta = {}
        if self.error:
            meta["error"] = True
        if self.meta_extra:
            meta.update(self.meta_extra)
        if full_response.strip():
            try:
                assistant_msg_id = await self._db.append_message(
                    uid,
                    "assistant",
                    full_response,
                    self.dek,
                    meta,
                    reply_to=self.reply_to,
                    created_date=self.date,
                )
                self.assistant_msg_id = assistant_msg_id
                logger.debug("Saved assistant message %s", assistant_msg_id)
            except Exception:
                logger.exception("Failed to save assistant message")
                full_response += (
                    "<span class='error'>⚠️ Failed to save response.</span>"
                )
                self.error = True
        async with self._cond:
            self.text = full_response
            self.meta = meta
            self.done = True
            self._cond.notify_all()
        self._invoke_cleanup()

    def _invoke_cleanup(self) -> None:
        if not self._cleanup_called:
            self._cleanup_called = True
            try:
                self._cleanup(self.user_msg_id)
            except Exception:
                logger.exception("Cleanup callback failed for %s", self.user_msg_id)

    async def cancel(self) -> None:
        self.cancelled = True
        await self._llm.abort(self.user_msg_id)
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
        llm: LLMEngine,
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


__all__ = ["PendingResponse", "ChatStreamManager"]
