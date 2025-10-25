import logging
import os
from typing import Any, AsyncGenerator

import httpx
import orjson
from httpx import HTTPError

from config import DEFAULT_LLM_REQUEST, GRAMMAR_FILE
from llm.prompt_template import build_prompt

from .process_manager import LlamafileProcessManager


class LLMClient:
    """Client responsible for interacting with the llamafile HTTP API."""

    def __init__(
        self,
        process_manager: LlamafileProcessManager,
        default_request: dict | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.process_manager = process_manager
        self.default_request = {**DEFAULT_LLM_REQUEST, **(default_request or {})}
        self.ctx_size = process_manager.ctx_size
        self._active_streams: dict[str, httpx.Response] = {}

        grammar_path = os.path.abspath(GRAMMAR_FILE)
        with open(grammar_path, "r", encoding="utf-8") as gf:
            self.grammar = gf.read()

    @property
    def server_url(self) -> str:
        return self.process_manager.base_url()

    def shutdown(self) -> None:
        self.process_manager.shutdown()

    async def _count_tokens(self, client: httpx.AsyncClient, text: str) -> int:
        resp = await client.post(f"{self.server_url}/tokenize", json={"content": text})
        resp.raise_for_status()
        return len(resp.json().get("tokens", []))

    async def _trim_history(
        self, history: list[dict[str, Any]], max_input: int, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not history:
            return history
        async with httpx.AsyncClient(timeout=None) as client:
            lo, hi = 0, len(history)
            while lo < hi:
                mid = (lo + hi) // 2
                slice_history = history[mid:]
                prompt = build_prompt(slice_history, **context)
                tokens = await self._count_tokens(client, prompt)
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
                prompt = build_prompt(history, **ctx)
            except Exception as e:
                self.logger.exception("Failed to build prompt")
                yield {"type": "error", "data": f"Prompt error: {e}"}
                return

        payload = {"prompt": prompt, **cfg, "grammar": self.grammar}

        transport = httpx.AsyncHTTPTransport(retries=0)
        headers = {
            "Accept": "text/event-stream",
            "Connection": "keep-alive",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        }

        async with httpx.AsyncClient(timeout=None, transport=transport) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.server_url}/completion",
                    json=payload,
                    headers=headers,
                ) as resp:
                    self._active_streams[msg_id] = resp
                    resp.raise_for_status()

                    event_buf: list[str] = []
                    saw_stop = False
                    saw_content = False

                    async for line in resp.aiter_lines():
                        if line is None:
                            continue
                        if line.startswith(":"):
                            continue  # SSE comment/heartbeat
                        if line.startswith("data:"):
                            event_buf.append(line[5:].lstrip())
                            continue
                        if line == "":
                            if not event_buf:
                                continue
                            data_str = "\n".join(event_buf).strip()
                            event_buf.clear()
                            try:
                                data = orjson.loads(data_str)
                            except Exception:
                                continue
                            if data.get("stop"):
                                saw_stop = True
                                break
                            content = data.get("content")
                            if content:
                                saw_content = True
                                yield content

                    if event_buf:
                        try:
                            data = orjson.loads("\n".join(event_buf).strip())
                            if data.get("stop"):
                                saw_stop = True
                            else:
                                content = data.get("content")
                                if content and content.strip():
                                    saw_content = True
                                    yield content
                        except Exception:
                            pass

                    if not saw_stop:
                        msg = (
                            "Stream ended unexpectedly"
                            if saw_content
                            else "LLM server disconnected"
                        )
                        yield {"type": "error", "data": msg}
                    return
            except HTTPError as e:
                self.process_manager.ensure_server_running()
                yield {"type": "error", "data": f"HTTP error: {e}"}
                return
            except Exception as e:
                yield {"type": "error", "data": f"Unexpected error: {e}"}
                return
            finally:
                self._active_streams.pop(msg_id, None)

    async def abort(self, msg_id: str) -> bool:
        resp = self._active_streams.pop(msg_id, None)
        if resp is not None:
            self.logger.info("Aborting stream %s", msg_id)
            try:
                await resp.aclose()
            except Exception:
                self.logger.exception("Error closing stream %s", msg_id)
            return True
        else:
            self.logger.debug("No active stream to abort for %s", msg_id)
            return False
