import asyncio
import hashlib
import logging
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncGenerator, NamedTuple

import httpx
import orjson
from httpx import HTTPError

from cachetools import LRUCache
from llamora.settings import settings
from llamora.util import resolve_data_path
from .process_manager import LlamafileProcessManager
from .prompt_template import build_prompt
from .tokenizers.tokenizer import count_tokens as qwen_count_tokens

LLM_DIR = Path(__file__).resolve().parent
GRAMMAR_PATH = resolve_data_path(
    settings.PROMPTS.grammar_file, fallback_dir=LLM_DIR
)
DEFAULT_LLM_REQUEST = dict(settings.LLM.request)
HISTORY_TOKEN_CACHE_SIZE = 32


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


class _CompletionStream:
    """Manage the background SSE completion stream and expose an iterator."""

    def __init__(self, client: "LLMClient", payload: dict[str, Any]) -> None:
        self._client = client
        self._payload = payload
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._sentinel = object()
        self.task: asyncio.Task[None] = asyncio.create_task(self._run())

    async def _emit(self, item: Any) -> None:
        await self._queue.put(item)

    def _emit_nowait(self, item: Any) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def _run(self) -> None:
        parser = SSEStreamParser()
        stopped = False
        try:
            async with self._client._client.stream(
                "POST",
                f"{self._client.server_url}/completion",
                json=self._payload,
                headers=self._client._sse_headers,
            ) as resp:
                resp.raise_for_status()

                async for line in resp.aiter_lines():
                    events = parser.feed_line(line)
                    for event in events:
                        if event.type == "content" and event.data is not None:
                            await self._emit(event.data)
                        elif event.type == "stop":
                            stopped = True
                            break
                    if stopped:
                        break

                if not stopped:
                    for event in parser.finalize():
                        if event.type == "content" and event.data is not None:
                            await self._emit(event.data)
                        elif event.type == "stop":
                            stopped = True

                if not parser.saw_stop:
                    msg = (
                        "Stream ended unexpectedly"
                        if parser.saw_content
                        else "LLM server disconnected"
                    )
                    await self._emit({"type": "error", "data": msg})
        except HTTPError as e:
            self._client.process_manager.ensure_server_running()
            await self._emit({"type": "error", "data": f"HTTP error: {e}"})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._emit({"type": "error", "data": f"Unexpected error: {e}"})
        finally:
            self._emit_nowait(self._sentinel)

    def __aiter__(self) -> "_CompletionStream":
        return self

    async def __anext__(self) -> Any:
        item = await self._queue.get()
        if item is self._sentinel:
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        if not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


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
        # Cached cumulative token counts keyed by (history_hash, context_hash).
        # The cache lets adjacent requests within the same stream reuse
        # tokenisation results instead of repeatedly calling the HTTP endpoint.
        self._history_token_cache: LRUCache[
            tuple[str, str], tuple[int, ...]
        ] = LRUCache(maxsize=HISTORY_TOKEN_CACHE_SIZE)

        with open(GRAMMAR_PATH, "r", encoding="utf-8") as gf:
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
        return await asyncio.to_thread(qwen_count_tokens, text)

    @staticmethod
    def _fingerprint(data: Any) -> str:
        def _default(obj: Any) -> str:
            return repr(obj)

        payload = orjson.dumps(
            data, option=orjson.OPT_SORT_KEYS, default=_default
        )
        return hashlib.blake2b(payload, digest_size=16).hexdigest()

    def _token_cache_key(self, history: list[dict[str, Any]], context: dict[str, Any]) -> tuple[str, str]:
        history_hash = self._fingerprint(history)
        context_hash = self._fingerprint(context)
        return history_hash, context_hash

    async def _get_token_counts(
        self, history: list[dict[str, Any]], context: dict[str, Any]
    ) -> tuple[int, ...]:
        """Return cached cumulative token counts for each history suffix.

        The cache is keyed by a stable hash of the history and context values.
        Any mutation to either input results in a new key, automatically
        invalidating stale entries. Counts are eagerly computed for each suffix
        so that subsequent `_trim_history` calls can be serviced without extra
        HTTP tokenisation requests.
        """
        key = self._token_cache_key(history, context)
        cached = self._history_token_cache.get(key)
        if cached is not None and len(cached) == len(history):
            return cached

        counts = list(cached) if cached is not None else []
        for idx in range(len(counts), len(history)):
            prompt = build_prompt(history[idx:], **context)
            tokens = await self._count_tokens(prompt)
            counts.append(tokens)

        result = tuple(counts)
        self._history_token_cache[key] = result
        return result

    async def _trim_history(
        self, history: list[dict[str, Any]], max_input: int, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Trim the conversation history to respect the model context window.

        The function reuses cached token counts where possible, ensuring the
        number of tokenisation calls is bounded by the history length for a
        given history/context pair. Any change to the history or rendering
        context yields a different cache key, forcing token counts to be
        recomputed for the new inputs.
        """
        if not history:
            return history
        ctx = dict(context)
        token_counts = await self._get_token_counts(history, ctx)

        for start_idx, tokens in enumerate(token_counts):
            if tokens <= max_input:
                return history[start_idx:]
        return []

    async def stream_response(
        self,
        msg_id: str,
        history: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        prompt: str | None = None,
    ) -> AsyncGenerator[Any, None]:
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
                prompt = build_prompt(history, **ctx)
            except Exception as e:
                self.logger.exception("Failed to build prompt")
                yield {"type": "error", "data": f"Prompt error: {e}"}
                return

        payload = {"prompt": prompt, **cfg, "grammar": self.grammar}

        stream = _CompletionStream(self, payload)

        try:
            async with self._track_stream(msg_id, stream.task):
                async for item in stream:
                    yield item
        finally:
            await stream.aclose()

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

    @asynccontextmanager
    async def _track_stream(
        self, msg_id: str, task: asyncio.Task[None]
    ) -> AsyncGenerator[None, None]:
        async with self._streams_lock:
            self._active_streams[msg_id] = task
        try:
            yield
        finally:
            async with self._streams_lock:
                current = self._active_streams.get(msg_id)
                if current is task:
                    self._active_streams.pop(msg_id, None)
