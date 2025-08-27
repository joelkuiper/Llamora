import atexit
import orjson
import subprocess
import time
import logging
import threading
from typing import Any, AsyncGenerator

import sys

import httpx
from httpx import HTTPError

from config import DEFAULT_LLM_REQUEST, LLM_SERVER, GRAMMAR_FILE
from llm.prompt_template import build_prompt
import socket
import os
import signal


def _server_args_to_cli(args: dict[str, Any]) -> list[str]:
    cli_args: list[str] = []
    for k, v in args.items():
        if v is None or v is False:
            continue
        flag = f"--{k.replace('_', '-')}"
        if v is True:
            cli_args.append(flag)
        else:
            cli_args.extend([flag, str(v)])
    return cli_args


def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LLMEngine:
    """Manage a llamafile server process and stream responses."""

    def __init__(
        self,
        server_args: dict | None = None,
        default_request: dict | None = None,
    ):
        logger = logging.getLogger(__name__)
        self.logger = logger

        self.port = _find_free_port()
        self.restart_attempts = 0
        self.max_restarts = 3

        host = LLM_SERVER.get("host")
        llamafile_path = LLM_SERVER.get("llamafile_path")
        cfg_server_args = {**LLM_SERVER.get("args", {}), **(server_args or {})}

        self.default_request = {**DEFAULT_LLM_REQUEST, **(default_request or {})}

        self.ctx_size = cfg_server_args.get("ctx_size")

        self._active_streams: dict[str, httpx.Response] = {}

        grammar_path = os.path.abspath(GRAMMAR_FILE)
        with open(grammar_path, "r", encoding="utf-8") as gf:
            self.grammar = gf.read()

        if host:
            self.proc = None
            self.server_url = host
            self.cmd = None
            logger.info("Using external llama server at %s", host)
        else:
            if not llamafile_path:
                raise ValueError("LLAMORA_LLAMAFILE environment variable not set")

            self.cmd = [
                "sh",
                llamafile_path,
                "--server",
                "--nobrowser",
                "--port",
                str(self.port),
                *_server_args_to_cli(cfg_server_args),
            ]

            self.server_url = f"http://127.0.0.1:{self.port}"

            self._launch_server()
            atexit.register(self.shutdown)
            self._orig_signals: dict[int, signal.Handlers] = {}
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._orig_signals[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handle_exit)

    def _wait_until_ready(self) -> None:
        logger = logging.getLogger(__name__)
        for _ in range(100):
            try:
                resp = httpx.get(f"{self.server_url}/health", timeout=1.0)
                if resp.json().get("status") == "ok":
                    logger.info("Llamafile server responded with ok status")
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise RuntimeError("llamafile server failed to start")

    def _log_stream(self, stream, level: int) -> None:
        for line in iter(stream.readline, ""):
            if line:
                self.logger.log(level, line.rstrip())

    def _launch_server(self) -> None:
        if not getattr(self, "cmd", None):
            return
        self.logger.info("Starting llamafile with:" + " ".join(self.cmd))
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if self.proc.stdout:
            threading.Thread(
                target=self._log_stream,
                args=(self.proc.stdout, logging.INFO),
                daemon=True,
            ).start()
        if self.proc.stderr:
            threading.Thread(
                target=self._log_stream,
                args=(self.proc.stderr, logging.INFO),
                daemon=True,
            ).start()
        self._wait_until_ready()

    def _is_server_healthy(self) -> bool:
        try:
            resp = httpx.get(f"{self.server_url}/health", timeout=1.0)
            return resp.json().get("status") == "ok"
        except Exception:
            return False

    def _restart_server(self) -> None:
        if not getattr(self, "cmd", None):
            raise RuntimeError("Cannot restart external server")
        if self.restart_attempts >= self.max_restarts:
            raise RuntimeError("llamafile server repeatedly crashed")
        self.restart_attempts += 1
        self.logger.warning(
            "Restarting llamafile server (attempt %d)", self.restart_attempts
        )
        self.shutdown()
        self._launch_server()

    def _ensure_server_running(self) -> None:
        if self.proc is None:
            if not self._is_server_healthy():
                raise RuntimeError("llamafile server is unavailable")
            return
        if self.proc.poll() is not None or not self._is_server_healthy():
            self._restart_server()

    def shutdown(self) -> None:
        if getattr(self, "proc", None) and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - unlikely
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:  # process already gone
                pass

    def _handle_exit(self, signum, frame) -> None:  # pragma: no cover - signal handler
        try:
            self.shutdown()
        finally:
            handler = self._orig_signals.get(signum, signal.SIG_DFL)
            if handler in (signal.SIG_IGN, None):
                return
            if handler is signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)
            else:
                handler(signum, frame)

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass

    async def _count_tokens(self, client: httpx.AsyncClient, text: str) -> int:
        resp = await client.post(f"{self.server_url}/tokenize", json={"content": text})
        resp.raise_for_status()
        return len(resp.json().get("tokens", []))

    async def _trim_history(
        self, history: list[dict], max_input: int, context: dict
    ) -> list[dict]:
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
        history: list[dict] | None = None,
        params: dict | None = None,
        context: dict | None = None,
        prompt: str | None = None,
    ) -> AsyncGenerator[str, None]:
        self._ensure_server_running()

        cfg = {**self.default_request, **(params or {})}

        if prompt is None:
            history = history or []
            n_predict = cfg.get("n_predict")
            max_input = self.ctx_size - n_predict
            ctx = context or {}
            try:
                history = await self._trim_history(history, max_input, ctx)
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
                self._ensure_server_running()
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
