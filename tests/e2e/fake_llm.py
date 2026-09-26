"""Deterministic OpenAI-compatible LLM stub for end-to-end tests.

Serves the endpoints Llamora talks to upstream: ``/health``, ``/props`` and
``/v1/chat/completions`` (streaming and non-streaming). Structured-output
requests are answered by building an instance of the requested JSON schema,
so entry metadata, tag summaries and day summaries all get valid payloads.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_REPLY = "Thank you for sharing this. It sounds like a quiet, steady day."
DEFAULT_EMOJI = "🌿"
DEFAULT_TAGS = ["morning", "coffee"]
DEFAULT_SUMMARY = (
    "A calm day spent writing, with small moments of focus and reflection "
    "that added up to a sense of steady progress."
)


@dataclass(slots=True)
class FakeLLM:
    """Scriptable responses plus a log of the chat requests received."""

    reply: str = DEFAULT_REPLY
    emoji: str = DEFAULT_EMOJI
    tags: list[str] = field(default_factory=lambda: list(DEFAULT_TAGS))
    summary: str = DEFAULT_SUMMARY
    chunk_delay: float = 0.01
    # Failure modes: answer chat requests with this HTTP status, or stream
    # this many chunks and then an error event (as llama.cpp does mid-stream).
    fail_status: int | None = None
    fail_after: int | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "FakeLLM is not running"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> FakeLLM:
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-llm", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def reset(self) -> None:
        defaults = FakeLLM()
        self.reply = defaults.reply
        self.emoji = defaults.emoji
        self.tags = defaults.tags
        self.summary = defaults.summary
        self.chunk_delay = defaults.chunk_delay
        self.fail_status = None
        self.fail_after = None
        self.requests.clear()

    def chat_requests(self, *, structured: bool | None = None) -> list[dict[str, Any]]:
        """Recorded chat requests, optionally only (non-)structured ones."""
        if structured is None:
            return list(self.requests)
        return [r for r in self.requests if (_schema_of(r) is not None) == structured]

    # --- response construction -------------------------------------------

    def build_content(self, body: dict[str, Any]) -> str:
        schema = _schema_of(body)
        if schema is None:
            return self.reply
        return json.dumps(self._instance(schema, name=None), ensure_ascii=False)

    def _instance(self, schema: dict[str, Any], *, name: str | None) -> Any:
        kind = schema.get("type")
        if kind == "object":
            props = schema.get("properties") or {}
            return {key: self._instance(sub, name=key) for key, sub in props.items()}
        if kind == "array":
            return list(self.tags)
        if kind == "string":
            if name == "emoji":
                return self.emoji
            text = self.summary
            min_len = int(schema.get("minLength") or 0)
            max_len = schema.get("maxLength")
            while len(text) < min_len:
                text = f"{text} {self.summary}"
            if max_len is not None:
                text = text[: int(max_len)].rstrip()
            return text
        if kind in {"integer", "number"}:
            return 0
        if kind == "boolean":
            return False
        return None


def _schema_of(body: dict[str, Any]) -> dict[str, Any] | None:
    # llama.cpp style: Llamora sends the schema top-level via extra_body.
    schema = body.get("json_schema")
    if isinstance(schema, dict):
        return schema.get("schema", schema)
    fmt = body.get("response_format")
    if isinstance(fmt, dict) and isinstance(fmt.get("json_schema"), dict):
        inner = fmt["json_schema"]
        return inner.get("schema", inner)
    return None


def _make_handler(fake: FakeLLM) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # keep test output quiet

        def _send_json(self, payload: Any, status: int = 200) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send_json({"status": "ok"})
            elif self.path == "/props":
                self._send_json({"n_ctx": 8192, "total_slots": 4})
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._send_json({"error": "not found"}, status=404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            fake.requests.append(body)
            if fake.fail_status is not None:
                self._send_json(
                    {
                        "error": {
                            "message": "fake upstream failure",
                            "code": fake.fail_status,
                        }
                    },
                    status=fake.fail_status,
                )
                return
            content = fake.build_content(body)
            if body.get("stream"):
                self._stream(content)
            else:
                self._send_json(_completion(content))

        def _stream(self, content: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for index, piece in enumerate(_chunks(content)):
                if fake.fail_after is not None and index >= fake.fail_after:
                    self._event(
                        {"error": {"message": "fake stream failure", "code": 500}}
                    )
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self._event(_chunk(piece))
                if fake.chunk_delay:
                    time.sleep(fake.chunk_delay)
            self._event(_chunk(None, finish="stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

        def _event(self, payload: dict[str, Any]) -> None:
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()

    return Handler


def _chunks(text: str) -> list[str]:
    words = text.split(" ")
    return [w if i == 0 else f" {w}" for i, w in enumerate(words)]


def _chunk(content: str | None, *, finish: str | None = None) -> dict[str, Any]:
    delta = {} if content is None else {"role": "assistant", "content": content}
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "fake",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _completion(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
