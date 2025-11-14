"""Application service and lifecycle helpers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from llamora.app.embed.model import async_embed_texts, _get_model

from llamora.persistence.local_db import LocalDB
from llamora.app.api.search import SearchAPI
from llamora.app.services.lexical_reranker import LexicalReranker
from llamora.app.services.llm_service import LLMService
from llamora.app.services.llm_stream_config import LLMStreamConfig
from llamora.app.services.tag_service import TagService
from llamora.app.services.service_pulse import ServicePulse
from llamora.app.services.search_config import SearchConfig
from llamora.app.services.vector_search import VectorSearchService
from llamora.settings import settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppServices:
    """Bundle long-lived application services."""

    db: LocalDB
    vector_search: VectorSearchService
    lexical_reranker: LexicalReranker
    tag_service: TagService
    search_api: SearchAPI
    llm_service: LLMService
    service_pulse: ServicePulse

    @classmethod
    def create(cls) -> "AppServices":
        db = LocalDB()
        service_pulse = ServicePulse()
        search_config = SearchConfig.from_settings(settings)
        vector_search = VectorSearchService(db, search_config)
        lexical_reranker = LexicalReranker()
        tag_service = TagService(db)
        search_api = SearchAPI(
            db,
            vector_search,
            lexical_reranker,
            config=search_config,
            service_pulse=service_pulse,
            tag_service=tag_service,
        )
        stream_config = LLMStreamConfig.from_settings(settings)
        llm_service = LLMService(
            db,
            stream_config=stream_config,
            service_pulse=service_pulse,
        )
        db.set_search_api(search_api)
        return cls(
            db=db,
            vector_search=vector_search,
            lexical_reranker=lexical_reranker,
            tag_service=tag_service,
            search_api=search_api,
            llm_service=llm_service,
            service_pulse=service_pulse,
        )


class AppLifecycle:
    """Manage startup and shutdown of long-lived application services."""

    def __init__(
        self,
        services: AppServices,
        dek_store: Any,
        maintenance_interval: float = 60.0,
    ) -> None:
        self._services = services
        self._dek_store = dek_store
        self._maintenance_interval = maintenance_interval
        self._maintenance_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._started = False

    async def __aenter__(self) -> "AppLifecycle":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        """Start services and background maintenance."""

        async with self._lock:
            if self._started:
                return

            logger.debug(
                "Starting application lifecycle: db.init -> search_api.start -> llm_service.ensure_started"
            )
            db_initialised = False
            search_started = False
            llm_started = False

            try:
                await self._services.db.init()
                db_initialised = True
                await self._services.search_api.start()
                search_started = True
                await self._services.llm_service.ensure_started()
                llm_started = True
            except Exception:
                logger.debug(
                    "Startup failed; rolling back initialised services", exc_info=True
                )
                with suppress(Exception):
                    if search_started:
                        logger.debug(
                            "Rollback: stopping search API after startup failure"
                        )
                        await self._services.search_api.stop()
                with suppress(Exception):
                    if llm_started:
                        logger.debug(
                            "Rollback: stopping LLM service after startup failure"
                        )
                        await self._services.llm_service.ensure_stopped()
                with suppress(Exception):
                    if db_initialised:
                        logger.debug("Rollback: closing database after startup failure")
                        await self._services.db.close()
                raise

            asyncio.create_task(
                _warmup_embeddings(),
                name="llamora-embedding-warmup",
            )
            self._maintenance_task = asyncio.create_task(
                self._maintenance_loop(),
                name="llamora-maintenance",
            )
            self._started = True
            logger.info("Application lifecycle started")

    async def stop(self) -> None:
        """Stop services and cancel background maintenance."""

        maintenance_task: asyncio.Task | None
        async with self._lock:
            if not self._started:
                return

            logger.debug(
                "Stopping application lifecycle: cancel maintenance -> llm_service.ensure_stopped -> search_api.stop -> db.close"
            )
            maintenance_task = self._maintenance_task
            self._maintenance_task = None
            self._started = False

        if maintenance_task is not None:
            maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance_task

        errors: list[Exception] = []

        try:
            await self._services.llm_service.ensure_stopped()
        except Exception as exc:  # pragma: no cover - defensive logging occurs below
            logger.exception("Failed to stop LLM service cleanly")
            errors.append(exc)

        try:
            await self._services.search_api.stop()
        except Exception as exc:  # pragma: no cover - defensive logging occurs below
            logger.exception("Failed to stop search API cleanly")
            errors.append(exc)

        try:
            await self._services.db.close()
        except Exception as exc:
            logger.exception("Failed to close database cleanly")
            errors.append(exc)

        if errors:
            raise errors[0]

        logger.info("Application lifecycle stopped")

    async def _maintenance_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._maintenance_interval)
                with suppress(Exception):
                    self._dek_store.expire()
                try:
                    await self._services.search_api.maintenance_tick()
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("Search maintenance tick failed")
        except asyncio.CancelledError:
            logger.debug("Maintenance loop cancelled")
            raise

    @property
    def services(self) -> AppServices:
        return self._services


def get_services() -> AppServices:
    """Return the lazily initialised :class:`AppServices` container."""

    from quart import current_app

    services = current_app.extensions.get("llamora")
    if services is None:
        raise RuntimeError("App services container is not initialised")
    return services


def get_db() -> LocalDB:
    """Convenience accessor for the application database."""

    return get_services().db


def get_search_api() -> SearchAPI:
    """Convenience accessor for the search API service."""

    return get_services().search_api


def get_llm_service() -> LLMService:
    """Convenience accessor for the LLM service wrapper."""

    return get_services().llm_service


def get_tag_service() -> TagService:
    """Convenience accessor for the tag service."""

    return get_services().tag_service


async def _warmup_embeddings() -> None:
    """Prime the embedding model cache in the background."""

    cache_info = _get_model.cache_info()

    if cache_info.currsize > 0:
        logger.debug("Skipping embedding warm-up; model already cached")
        return

    logger.debug("Starting embedding warm-up")
    try:
        await async_embed_texts(["warmup"])
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Embedding warm-up failed")
    else:
        logger.debug("Embedding warm-up completed")
