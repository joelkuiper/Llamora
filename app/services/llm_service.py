"""LLM service wrapper managing the llamafile client stack."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from llm.client import LLMClient
from llm.process_manager import LlamafileProcessManager

from .chat_stream import ChatStreamManager


class LLMService:
    """Own the llamafile process, client, and chat streaming manager."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._process_manager: LlamafileProcessManager | None = None
        self._llm: LLMClient | None = None
        self._chat_stream_manager: ChatStreamManager | None = None
        self._lock: asyncio.Lock | None = None

    async def start(self) -> None:
        """Initialise the llamafile stack if it is not already running."""

        if self._llm is not None:
            return

        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            if self._llm is not None:
                return

            process_manager = LlamafileProcessManager()
            await asyncio.to_thread(process_manager.ensure_server_running)
            llm_client = LLMClient(process_manager)

            chat_stream_manager = ChatStreamManager(llm_client)
            chat_stream_manager.set_db(self._db)

            self._process_manager = process_manager
            self._llm = llm_client
            self._chat_stream_manager = chat_stream_manager

    async def stop(self) -> None:
        """Tear down the llamafile stack."""

        lock = self._lock
        if lock is None:
            self._lock = asyncio.Lock()
            lock = self._lock

        async with lock:
            chat_stream_manager = self._chat_stream_manager
            llm_client = self._llm
            process_manager = self._process_manager

            self._chat_stream_manager = None
            self._llm = None
            self._process_manager = None

        if chat_stream_manager is not None:
            await chat_stream_manager.shutdown()

        if llm_client is not None:
            for msg_id in list(getattr(llm_client, "_active_streams", {}).keys()):
                with suppress(Exception):
                    await llm_client.abort(msg_id)

        if process_manager is not None:
            await asyncio.to_thread(process_manager.shutdown)

    @property
    def process_manager(self) -> LlamafileProcessManager:
        if self._process_manager is None:
            raise RuntimeError("LLM service has not been started")
        return self._process_manager

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            raise RuntimeError("LLM service has not been started")
        return self._llm

    @property
    def chat_stream_manager(self) -> ChatStreamManager:
        if self._chat_stream_manager is None:
            raise RuntimeError("LLM service has not been started")
        return self._chat_stream_manager


__all__ = ["LLMService"]
