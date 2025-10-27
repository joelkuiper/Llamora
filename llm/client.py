import asyncio
import logging
import os
from contextlib import suppress
from typing import Any, AsyncGenerator, NamedTuple

import httpx
import orjson
from httpx import HTTPError

from config import DEFAULT_LLM_REQUEST, GRAMMAR_FILE
from .process_manager import LlamafileProcessManager


class SSEEvent(NamedTuple):
    """Represents a parsed server-sent event from the LLM stream."""

    type: str
    data: str | None = None


class SSEStreamParser:
    """Parse Server-Sent Events emitted by the LLM completion endpoint."""

    def __init__(self) -> None:
        self._event_buf: list[str] = []
        self.saw_stop = False
        self.saw_content = False

    def feed_line(self, line: str | None) -> list[SSEEvent]:
        """Consume a single line from the SSE stream and emit parsed events."""

        if line is None or line.startswith(":"):
            return []
        if line.startswith("data:"):
            self._event_buf.append(line[5:].lstrip())
            return []
        if line == "":
            event = self._flush_event()
            return [event] if event else []
        return []

    def finalize(self) -> list[SSEEvent]:
        """Flush any buffered event when the HTTP stream closes."""

        event = self._flush_event()
        return [event] if event else []

    def _flush_event(self) -> SSEEvent | None:
        if not self._event_buf:
            return None
        data_str = "\n".join(self._event_buf).strip()
        self._event_buf.clear()
        if not data_str:
            return None
        try:
            payload = orjson.loads(data_str)
        except Exception:
            return None
        if payload.get("stop"):
            self.saw_stop = True
            return SSEEvent("stop")
        content = payload.get("content")
        if content:
            self.saw_content = True
            return SSEEvent("content", content)
        return None


class LLMClient:
    """Client responsible for interacting with the llamafile HTTP API."""

    def __init__(
        self,
        process_manager: "LlamafileProcessManager",
        default_request: dict | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.process_manager = process_manager
        self.default_request = {**DEFAULT_LLM_REQUEST, **(default_request or {})}
        self.ctx_size = process_manager.ctx_size
        self._client = httpx.AsyncClient(
            timeout=None, transport=httpx.AsyncHTTPTransport(retries=0)
        )
        self._active_streams: dict[str, asyncio.Task[None]] = {}
        self._streams_lock = asyncio.Lock()
        self._sse_headers = {
            "Accept": "text/event-stream",
            "Connection": "keep-alive",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        }

        grammar_path = os.path.abspath(GRAMMAR_FILE)
        with open(grammar_path, "r", encoding="utf-8") as gf:
            self.grammar = gf.read()

    @property
    def server_url(self) -> str:
        return self.process_manager.base_url()

    def shutdown(self) -> None:
        self.process_manager.shutdown()

    async def aclose(self) -> None:
        async with self._streams_lock:
            tasks = list(self._active_streams.values())
            self._active_streams.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await self._client.aclose()

    async def _count_tokens(self, text: str) -> int:
        resp = await self._client.post(
            f"{self.server_url}/tokenize", json={"content": text}
        )
        resp.raise_for_status()
        return len(resp.json().get("tokens", []))

    async def _trim_history(
        self, history: list[dict[str, Any]], max_input: int, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not history:
            return history
        from llm.prompt_template import build_prompt

        lo, hi = 0, len(history)
        while lo < hi:
            mid = (lo + hi) // 2
            slice_history = history[mid:]
            prompt = build_prompt(slice_history, **context)
            tokens = await self._count_tokens(prompt)
            if tokens <= max_input:
                hi = mid
            else:
                lo = mid + 1
        return history[lo:]

    async def stream_response(
        self,
        msg_id: str,
        history: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        prompt: str | None = None,
    ) -> AsyncGenerator[str, None]:
        self.process_manager.ensure_server_running()

        cfg = {**self.default_request, **(params or {})}

        if prompt is None:
            history = history or []
            ctx = context or {}
            n_predict = cfg.get("n_predict")
            if self.ctx_size is not None and n_predict is not None:
                max_input = self.ctx_size - n_predict
                try:
                    history = await self._trim_history(history, max_input, ctx)
                except Exception as e:
                    self.logger.exception("Failed to trim history")
                    yield {"type": "error", "data": f"Prompt error: {e}"}
                    return
            try:
                from llm.prompt_template import build_prompt

                prompt = build_prompt(history, **ctx)
            except Exception as e:
                self.logger.exception("Failed to build prompt")
                yield {"type": "error", "data": f"Prompt error: {e}"}
                return

        payload = {"prompt": prompt, **cfg, "grammar": self.grammar}

        queue: asyncio.Queue[Any] = asyncio.Queue()
        sentinel = object()

        async def _emit(item: Any) -> None:
            await queue.put(item)

        def _emit_nowait(item: Any) -> None:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

        parser = SSEStreamParser()
        stopped = False

        async def _run_completion() -> None:
            nonlocal stopped
            try:
                async with self._client.stream(
                    "POST",
                    f"{self.server_url}/completion",
                    json=payload,
                    headers=self._sse_headers,
                ) as resp:
                    resp.raise_for_status()

                    async for line in resp.aiter_lines():
                        events = parser.feed_line(line)
                        for event in events:
                            if event.type == "content" and event.data is not None:
                                await _emit(event.data)
                            elif event.type == "stop":
                                stopped = True
                                break
                        if stopped:
                            break

                    if not stopped:
                        for event in parser.finalize():
                            if event.type == "content" and event.data is not None:
                                await _emit(event.data)
                            elif event.type == "stop":
                                stopped = True

                    if not parser.saw_stop:
                        msg = (
                            "Stream ended unexpectedly"
                            if parser.saw_content
                            else "LLM server disconnected"
                        )
                        await _emit({"type": "error", "data": msg})
            except HTTPError as e:
                self.process_manager.ensure_server_running()
                await _emit({"type": "error", "data": f"HTTP error: {e}"})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                await _emit({"type": "error", "data": f"Unexpected error: {e}"})
            finally:
                _emit_nowait(sentinel)

        runner_task = asyncio.create_task(_run_completion())
        async with self._streams_lock:
            self._active_streams[msg_id] = runner_task

        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item
        finally:
            if not runner_task.done():
                runner_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runner_task
            async with self._streams_lock:
                current = self._active_streams.get(msg_id)
                if current is runner_task:
                    self._active_streams.pop(msg_id, None)

    async def abort(self, msg_id: str) -> bool:
        async with self._streams_lock:
            task = self._active_streams.pop(msg_id, None)
        if task is not None:
            self.logger.info("Aborting stream %s", msg_id)
            try:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            except Exception:
                self.logger.exception("Error closing stream %s", msg_id)
            return True
        else:
            self.logger.debug("No active stream to abort for %s", msg_id)
            return False
