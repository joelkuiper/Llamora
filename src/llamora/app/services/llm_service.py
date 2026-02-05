"""LLM service wrapper managing the OpenAI-compatible client stack."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from llamora.llm.client import LLMClient
from llamora.llm.upstream_manager import UpstreamProcessManager

from .response_stream import ResponseStreamManager
from .llm_stream_config import LLMStreamConfig
from .service_pulse import ServicePulse


logger = logging.getLogger(__name__)


class LLMService:
    """Own the OpenAI-compatible client and optional local upstream."""

    def __init__(
        self,
        db: Any,
        *,
        stream_config: LLMStreamConfig,
        service_pulse: ServicePulse | None = None,
    ) -> None:
        self._db = db
        self._upstream_manager: UpstreamProcessManager | None = None
        self._llm: LLMClient | None = None
        self._response_stream_manager: ResponseStreamManager | None = None
        self._lock = asyncio.Lock()
        self._stream_config = stream_config
        self._service_pulse = service_pulse

    async def start(self) -> None:
        """Initialise the LLM stack if it is not already running."""

        await self.ensure_started()

    async def stop(self) -> None:
        """Tear down the LLM stack."""

        await self.ensure_stopped()

    async def ensure_started(self) -> None:
        """Ensure that the LLM stack has been started."""

        if self._llm is not None:
            return

        async with self._lock:
            if self._llm is not None:
                return

            logger.debug("Initialising LLM service stack")

            upstream_manager: UpstreamProcessManager | None = None
            llm_client: LLMClient | None = None
            response_stream_manager: ResponseStreamManager | None = None

            try:
                upstream_manager = UpstreamProcessManager()
                await asyncio.to_thread(upstream_manager.ensure_upstream_ready)
                llm_client = LLMClient(
                    upstream_manager, service_pulse=self._service_pulse
                )

                response_stream_manager = ResponseStreamManager(
                    llm_client,
                    stream_config=self._stream_config,
                    service_pulse=self._service_pulse,
                )
                response_stream_manager.set_db(self._db)
            except Exception:
                logger.exception("Failed to initialise LLM service stack")

                if response_stream_manager is not None:
                    try:
                        await response_stream_manager.shutdown()
                    except Exception:
                        logger.exception(
                            "Error shutting down response stream manager after failed start"
                        )

                if llm_client is not None:
                    try:
                        await llm_client.aclose()
                    except Exception:
                        logger.exception("Error closing LLM client after failed start")

                if upstream_manager is not None:
                    try:
                        await asyncio.to_thread(upstream_manager.shutdown)
                    except Exception:
                        logger.exception(
                            "Error shutting down upstream manager after failed start"
                        )

                raise

            self._upstream_manager = upstream_manager
            self._llm = llm_client
            self._response_stream_manager = response_stream_manager

            logger.info("LLM service stack started")

    async def ensure_stopped(self) -> None:
        """Ensure that the LLM stack has been stopped."""

        async with self._lock:
            if self._llm is None and self._upstream_manager is None:
                return

            logger.debug("Tearing down LLM service stack")

            response_stream_manager = self._response_stream_manager
            llm_client = self._llm
            upstream_manager = self._upstream_manager

            self._response_stream_manager = None
            self._llm = None
            self._upstream_manager = None

        errors: list[Exception] = []

        if response_stream_manager is not None:
            try:
                await response_stream_manager.shutdown()
            except Exception as exc:
                errors.append(exc)
                logger.exception("Error shutting down response stream manager")

        if llm_client is not None:
            try:
                await llm_client.aclose()
            except Exception as exc:
                errors.append(exc)
                logger.exception("Error closing LLM client")

        if upstream_manager is not None:
            try:
                await asyncio.to_thread(upstream_manager.shutdown)
            except Exception as exc:
                errors.append(exc)
                logger.exception("Error shutting down upstream manager")

        if errors:
            if len(errors) == 1:
                raise errors[0]
            raise ExceptionGroup("Errors while stopping LLM service", errors)

        logger.info("LLM service stack stopped")

    @property
    def upstream_manager(self) -> UpstreamProcessManager:
        if self._upstream_manager is None:
            raise RuntimeError("LLM service has not been started")
        return self._upstream_manager

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            raise RuntimeError("LLM service has not been started")
        return self._llm

    @property
    def response_stream_manager(self) -> ResponseStreamManager:
        if self._response_stream_manager is None:
            raise RuntimeError("LLM service has not been started")
        return self._response_stream_manager


__all__ = ["LLMService"]
