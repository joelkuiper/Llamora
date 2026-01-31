from __future__ import annotations

import importlib
import logging
import time
from typing import Any, Callable, Sequence, Tuple, Type

import orjson

from llamora.app.services.index_worker import IndexWorker
from llamora.app.services.lexical_reranker import LexicalReranker
from llamora.app.services.search_config import SearchConfig
from llamora.app.services.search_pipeline import (
    BaseSearchCandidateGenerator,
    BaseSearchNormalizer,
    BaseSearchReranker,
    BaseTagEnricher,
    DefaultSearchCandidateGenerator,
    DefaultSearchNormalizer,
    DefaultSearchReranker,
    DefaultTagEnricher,
    InvalidSearchQuery,
    SearchPipeline,
    SearchPipelineComponents,
    SearchPipelineResult,
)
from llamora.app.services.service_pulse import ServicePulse
from llamora.app.services.tag_service import TagService
from llamora.app.services.search_stream import (
    SearchStreamManager,
    SearchStreamResult,
)
from llamora.app.services.vector_search import VectorSearchService
from llamora.settings import settings

logger = logging.getLogger(__name__)

IndexJob = Tuple[str, str, str, bytes]


class SearchAPI:
    """High level search interface operating on encrypted messages."""

    def __init__(
        self,
        db,
        vector_search: VectorSearchService | None = None,
        lexical_reranker: LexicalReranker | None = None,
        normalizer: BaseSearchNormalizer | None = None,
        candidate_generator: BaseSearchCandidateGenerator | None = None,
        tag_enricher: BaseTagEnricher | None = None,
        reranker: BaseSearchReranker | None = None,
        *,
        config: SearchConfig | None = None,
        service_pulse: ServicePulse | None = None,
        tag_service: TagService | None = None,
    ) -> None:
        self.db = db
        self.config = config or SearchConfig.from_settings(settings)
        self.vector_search = vector_search or VectorSearchService(db, self.config)
        self._tag_service = tag_service or TagService(db)
        self._service_pulse = service_pulse
        self._pipeline_overrides = self._read_pipeline_overrides(settings)

        lexical_reranker = lexical_reranker or LexicalReranker()
        components = self._build_pipeline_components(
            lexical_reranker,
            normalizer,
            candidate_generator,
            tag_enricher,
            reranker,
        )
        self._pipeline = SearchPipeline(components)

        reranker_component = components.reranker
        self.lexical_reranker = getattr(
            reranker_component,
            "lexical_reranker",
            lexical_reranker,
        )

        self._stream_manager = SearchStreamManager(
            vector_search=self.vector_search,
            pipeline_components=components,
            config=self.config,
            stream_ttl=float(getattr(settings.SEARCH, "stream_ttl", 900)),
            stream_max_sessions=int(getattr(settings.SEARCH, "stream_max_sessions", 200)),
        )

        self._emit_config_diagnostics()
        self._index_worker = IndexWorker(
            self,
            search_config=self.config,
            max_queue_size=int(settings.WORKERS.index_worker.max_queue_size),
            batch_size=int(settings.WORKERS.index_worker.batch_size),
            flush_interval=float(settings.WORKERS.index_worker.flush_interval),
        )

    async def warm_index(self, user_id: str, dek: bytes) -> None:
        """Ensure the vector index for ``user_id`` is resident in memory."""

        start = time.perf_counter()
        logger.debug("Pre-warming vector index for user %s", user_id)
        try:
            await self.vector_search.index_store.ensure_index(user_id, dek)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Vector index warm-up failed for user %s", user_id)
            return

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Vector index warm-up completed for user %s in %.1fms",
            user_id,
            elapsed_ms,
        )

    async def start(self) -> None:
        """Start background services for the search API."""

        await self._index_worker.start()

    async def stop(self) -> None:
        """Stop background services for the search API."""

        await self._index_worker.stop()

    async def enqueue_index_job(
        self,
        user_id: str,
        message_id: str,
        plaintext: str,
        dek: bytes,
    ) -> None:
        await self._index_worker.enqueue(user_id, message_id, plaintext, dek)

    async def bulk_index(self, jobs: Sequence[IndexJob]) -> None:
        if not jobs:
            return

        start = time.perf_counter()
        decode_fallbacks = 0
        parsed: list[IndexJob] = []
        for user_id, msg_id, plaintext, dek in jobs:
            content = plaintext
            try:
                record = orjson.loads(plaintext)
            except orjson.JSONDecodeError:
                decode_fallbacks += 1
                logger.debug(
                    "Failed to decode plaintext for message %s (user %s)",
                    msg_id,
                    user_id,
                )
            else:
                content = record.get("message", content)
            parsed.append((user_id, msg_id, content, dek))

        await self.vector_search.index_store.bulk_index(parsed)

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Bulk indexed %d messages for %d users in %.1fms (decode_fallbacks=%d, dropped=%d)",
            len(parsed),
            len({job[0] for job in parsed}),
            elapsed_ms,
            decode_fallbacks,
            self._index_worker.dropped_jobs,
        )

    async def search(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        k1: int | None = None,
        k2: int | None = None,
    ) -> tuple[str, list[dict], bool]:
        cfg = self.config.progressive
        resolved_k1 = int(k1) if k1 is not None else cfg.k1
        resolved_k2 = int(k2) if k2 is not None else cfg.k2
        logger.debug(
            "Search requested by user %s with k1=%d k2=%d",
            user_id,
            resolved_k1,
            resolved_k2,
        )

        result: SearchPipelineResult = await self._pipeline.execute(
            user_id,
            dek,
            query,
            resolved_k1,
            resolved_k2,
        )

        if not result.candidates:
            logger.debug(
                "No candidates found for user %s; returning empty result set",
                user_id,
            )
            return result.normalized.text, [], result.truncated

        await self._tag_service.hydrate_search_results(
            user_id,
            dek,
            result.results,
            result.enrichment.tokens,
        )
        logger.debug("Returning %d results for user %s", len(result.results), user_id)
        return result.normalized.text, result.results, result.truncated

    @property
    def search_config(self) -> SearchConfig:
        """Expose the active search configuration."""

        return self.config

    def _emit_config_diagnostics(self) -> None:
        payload = self.config.as_dict()
        if self._service_pulse is not None:
            self._service_pulse.emit("search.config", payload)
        logger.info(
            "Search configuration loaded: k1=%d k2=%d rounds=%d batch_size=%d",
            self.config.progressive.k1,
            self.config.progressive.k2,
            self.config.progressive.rounds,
            self.config.progressive.batch_size,
        )

    async def on_message_appended(
        self,
        user_id: str,
        msg_id: str,
        plaintext: str,
        dek: bytes,
    ) -> None:
        await self.bulk_index([(user_id, msg_id, plaintext, dek)])

    async def maintenance_tick(self) -> None:
        await self.vector_search.maintenance_tick()

    async def search_stream(
        self,
        user_id: str,
        dek: bytes,
        query: str,
        *,
        session_id: str | None,
        offset: int,
        page_limit: int,
        result_window: int,
        k1: int | None = None,
        k2: int | None = None,
    ) -> SearchStreamResult:
        """Incrementally fetch search results without computing the full window."""

        return await self._stream_manager.fetch_page(
            user_id=user_id,
            dek=dek,
            query=query,
            session_id=session_id,
            offset=offset,
            page_limit=page_limit,
            result_window=result_window,
            k1=k1,
            k2=k2,
        )
    def _build_pipeline_components(
        self,
        lexical_reranker: LexicalReranker,
        normalizer: BaseSearchNormalizer | None,
        candidate_generator: BaseSearchCandidateGenerator | None,
        tag_enricher: BaseTagEnricher | None,
        reranker: BaseSearchReranker | None,
    ) -> SearchPipelineComponents:
        component_builders: dict[
            str,
            tuple[
                Any | None,
                Type[Any],
                Callable[[], dict[str, Any]],
            ],
        ] = {
            "normalizer": (
                normalizer,
                DefaultSearchNormalizer,
                lambda: {"config": self.config},
            ),
            "candidate_generator": (
                candidate_generator,
                DefaultSearchCandidateGenerator,
                lambda: {
                    "vector_search": self.vector_search,
                    "config": self.config,
                },
            ),
            "tag_enricher": (
                tag_enricher,
                DefaultTagEnricher,
                lambda: {
                    "db": self.db,
                    "tag_service": self._tag_service,
                    "vector_search": self.vector_search,
                },
            ),
            "reranker": (
                reranker,
                DefaultSearchReranker,
                lambda: {"lexical_reranker": lexical_reranker},
            ),
        }

        components: dict[str, Any] = {}
        for key, (provided, default_cls, kwargs_factory) in component_builders.items():
            components[key] = self._resolve_component(
                provided,
                key,
                default_cls,
                kwargs_factory,
            )

        return SearchPipelineComponents(**components)

    def _resolve_component(
        self,
        provided: Any | None,
        key: str,
        default_cls: Type[Any],
        kwargs_factory: Callable[[], dict[str, Any]],
    ) -> Any:
        if provided is not None:
            return provided

        kwargs = kwargs_factory()
        override_cls = self._load_component_class(key)
        if override_cls is not None:
            try:
                return override_cls(**kwargs)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "Failed to initialise search pipeline override for %s", key
                )

        return default_cls(**kwargs)

    def _load_component_class(self, key: str) -> Type[Any] | None:
        path = self._pipeline_overrides.get(key)
        if not path:
            return None

        module_name, _, attr = path.rpartition(".")
        if not module_name or not attr:
            logger.error("Invalid search pipeline override path '%s' for %s", path, key)
            return None

        try:
            module = importlib.import_module(module_name)
            return getattr(module, attr)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "Failed to load search pipeline override for %s from %s", key, path
            )
            return None

    @staticmethod
    def _read_pipeline_overrides(settings_obj: Any) -> dict[str, str]:
        overrides: dict[str, str] = {}
        search_cfg = getattr(settings_obj, "SEARCH", None)
        if search_cfg is None:
            return overrides

        pipeline_cfg = getattr(search_cfg, "pipeline", None)
        if pipeline_cfg is None:
            return overrides

        keys = ("normalizer", "candidate_generator", "tag_enricher", "reranker")
        for key in keys:
            value: Any | None
            if isinstance(pipeline_cfg, dict):
                value = pipeline_cfg.get(key)
            else:
                value = getattr(pipeline_cfg, key, None)
                if value is None and hasattr(pipeline_cfg, "get"):
                    value = pipeline_cfg.get(key)
            if isinstance(value, str) and value:
                overrides[key] = value
        return overrides


__all__ = ["SearchAPI", "InvalidSearchQuery"]
