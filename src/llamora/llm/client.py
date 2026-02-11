from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any, AsyncGenerator, Mapping, Sequence

import orjson
from cachetools import LRUCache
from openai import APIError, APIStatusError, APITimeoutError, AsyncOpenAI

from llamora.app.util import canonicalize
from llamora.llm.budget import PromptBudget
from llamora.settings import settings

from .entry_template import build_entry_messages, estimate_entry_messages_tokens
from .tokenizers.tokenizer import history_suffix_token_totals
from .upstream_manager import UpstreamProcessManager

if TYPE_CHECKING:
    from llamora.app.services.service_pulse import ServicePulse

DEFAULT_LLM_GENERATION = dict(settings.LLM.generation)
HISTORY_TOKEN_CACHE_SIZE = 32


class _ChatStream:
    """Manage a background chat completion stream and expose an iterator."""

    def __init__(
        self,
        client: "LLMClient",
        payload: dict[str, Any],
    ) -> None:
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
        try:
            stream = await self._client._openai.chat.completions.create(**self._payload)
            async for chunk in stream:
                content = self._client._extract_stream_delta(chunk)
                if content:
                    await self._emit(content)
        except APIStatusError as exc:
            self._client.upstream.ensure_upstream_ready()
            detail = f"{exc.status_code} {exc.message}".strip()
            self._client.logger.error("Completion request failed: %s", detail)
            await self._emit({"type": "error", "data": detail})
        except APITimeoutError as exc:
            self._client.upstream.ensure_upstream_ready()
            await self._emit({"type": "error", "data": f"Timeout: {exc}"})
        except APIError as exc:
            self._client.upstream.ensure_upstream_ready()
            await self._emit({"type": "error", "data": f"API error: {exc}"})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._emit({"type": "error", "data": f"Unexpected error: {e}"})
        finally:
            self._emit_nowait(self._sentinel)

    def __aiter__(self) -> "_ChatStream":
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
    """Client responsible for interacting with an OpenAI-compatible chat API."""

    def __init__(
        self,
        upstream: UpstreamProcessManager,
        default_generation: dict | None = None,
        *,
        service_pulse: ServicePulse | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.upstream = upstream
        self.default_generation = {
            **DEFAULT_LLM_GENERATION,
            **(default_generation or {}),
        }
        self.ctx_size = upstream.ctx_size
        self.upstream_props = upstream.upstream_props
        self._chat_endpoint = self._normalize_chat_endpoint(
            settings.get("LLM.chat.endpoint", "/v1/chat/completions")
        )
        base_url = settings.get("LLM.chat.base_url")
        if not base_url:
            base_url = self._chat_base_url(self.upstream_url, self._chat_endpoint)
        from llamora.app.util.number import parse_positive_int, parse_positive_float

        timeout = parse_positive_float(settings.get("LLM.chat.timeout_seconds"))
        max_retries = parse_positive_int(settings.get("LLM.chat.max_retries"))
        self._openai = AsyncOpenAI(
            api_key=settings.get("LLM.chat.api_key") or "local",
            base_url=str(base_url),
            max_retries=max_retries if max_retries is not None else 0,
            timeout=timeout,
        )
        self._active_streams: dict[str, asyncio.Task[None]] = {}
        self._streams_lock = asyncio.Lock()
        self._active_slots: dict[str, int] = {}
        self._slots_released_by_abort: set[str] = set()
        self.parallel_slots = max(1, getattr(upstream, "parallel_slots", 1))
        self._slot_semaphore = asyncio.Semaphore(self.parallel_slots)
        self._slot_queue: asyncio.LifoQueue[int] = asyncio.LifoQueue()
        for slot_id in range(self.parallel_slots):
            self._slot_queue.put_nowait(slot_id)
        self.prompt_budget = PromptBudget(self, service_pulse=service_pulse)
        # Cached cumulative token counts keyed by (history_hash, context_hash).
        # The cache lets adjacent requests within the same stream reuse
        # tokenisation results instead of repeatedly calling the HTTP endpoint.
        self._history_token_cache: LRUCache[tuple[str, str], tuple[int, ...]] = (
            LRUCache(maxsize=HISTORY_TOKEN_CACHE_SIZE)
        )

    @staticmethod
    def _normalize_chat_endpoint(raw: Any) -> str:
        endpoint = str(raw or "/v1/chat/completions").strip()
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        return endpoint

    @staticmethod
    def _chat_base_url(upstream_url: str, endpoint: str) -> str:
        normalized = endpoint.strip()
        suffix = "/chat/completions"
        base_path = normalized
        if normalized.endswith(suffix):
            base_path = normalized[: -len(suffix)] or "/v1"
        upstream = upstream_url.rstrip("/")
        return f"{upstream}{base_path}"

    def _extract_stream_delta(self, chunk: Any) -> str | None:
        choices = getattr(chunk, "choices", None)
        if not choices:
            return None
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None)
            if content:
                return str(content)
        text = getattr(choice, "text", None)
        if text:
            return str(text)
        return None

    def _extract_chat_completion_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if content:
                return str(content).strip()
        text = getattr(choice, "text", None)
        return str(text).strip() if text else ""

    @property
    def upstream_url(self) -> str:
        return self.upstream.base_url()

    def shutdown(self) -> None:
        self.upstream.shutdown()

    async def aclose(self) -> None:
        async with self._streams_lock:
            tasks = list(self._active_streams.values())
            self._active_streams.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await self._openai.close()

    @staticmethod
    def _fingerprint(data: Any) -> str:
        def _default(obj: Any) -> str:
            return repr(obj)

        payload = orjson.dumps(
            data,
            option=getattr(orjson, "OPT_SORT_KEYS", 0),
            default=_default,
        )
        return hashlib.blake2b(payload, digest_size=16).hexdigest()

    def _token_cache_key(
        self, history: list[dict[str, Any]], context: dict[str, Any]
    ) -> tuple[str, str]:
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

        ctx = dict(context or {})
        totals = history_suffix_token_totals(history, context=ctx)
        result = tuple(totals)
        self._history_token_cache[key] = result
        return result

    @staticmethod
    def _canonicalize_tag_value(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return canonicalize(text)
        except ValueError:
            return None

    def _collect_tag_priorities(
        self, history: Sequence[Mapping[str, Any] | dict[str, Any]]
    ) -> tuple[dict[str, list[int]], dict[int, set[str]], dict[str, str]]:
        tag_occurrences: dict[str, list[int]] = {}
        tags_by_index: dict[int, set[str]] = {}
        tag_display: dict[str, str] = {}

        for idx, raw_entry in enumerate(history):
            entry = raw_entry if isinstance(raw_entry, Mapping) else dict(raw_entry)
            canonical_tags: set[str] = set()

            tags = entry.get("tags")
            if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes)):
                for raw_tag in tags:
                    if isinstance(raw_tag, Mapping):
                        candidate = raw_tag.get("name")
                    else:  # pragma: no cover - defensive
                        candidate = raw_tag
                    canonical_tag = self._canonicalize_tag_value(candidate)
                    if canonical_tag:
                        canonical_tags.add(canonical_tag)

            meta = entry.get("meta")
            if isinstance(meta, Mapping):
                tags = meta.get("tags")
                if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes)):
                    for tag in tags:
                        canonical_tag = self._canonicalize_tag_value(tag)
                        if canonical_tag:
                            canonical_tags.add(canonical_tag)

            if not canonical_tags:
                continue

            tags_by_index[idx] = canonical_tags

            for tag in canonical_tags:
                normalized = tag.lower()
                tag_occurrences.setdefault(normalized, []).append(idx)
                tag_display.setdefault(normalized, tag)

        return tag_occurrences, tags_by_index, tag_display

    async def _trim_history(
        self, history: list[dict[str, Any]], max_input: int, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Trim the entry history to respect the model context window.

        The function reuses cached token counts where possible, ensuring the
        number of tokenisation calls is bounded by the history length for a
        given history/context pair. Any change to the history or rendering
        context yields a different cache key, forcing token counts to be
        recomputed for the new inputs. When trimming drops messages that carry
        canonicalised tags (either attached by the user or emitted via
        ``meta.tags``), the function reintroduces the most recent instance
        for each tag so long as the combined prompt still fits within
        ``max_input`` tokens.
        """
        if not history:
            return history
        ctx = dict(context)
        token_counts = await self._get_token_counts(history, ctx)

        tag_occurrences, tags_by_index, tag_display = self._collect_tag_priorities(
            history
        )
        priority_targets = {
            indices[-1] for indices in tag_occurrences.values() if indices
        }

        base_start_idx: int | None = None
        for start_idx, tokens in enumerate(token_counts):
            if tokens <= max_input:
                base_start_idx = start_idx
                break

        if not priority_targets:
            if base_start_idx is not None:
                return history[base_start_idx:]
            return []

        base_indices = (
            list(range(base_start_idx, len(history)))
            if base_start_idx is not None
            else []
        )

        candidate_indices = sorted(set(base_indices) | priority_targets)

        async def total_tokens_for(indices: Sequence[int]) -> int:
            if not indices:
                return 0
            subset = [history[i] for i in indices]
            counts = await self._get_token_counts(subset, ctx)
            return counts[0] if counts else 0

        total_tokens = await total_tokens_for(candidate_indices)
        mutable_priority = set(priority_targets)
        removed_non_priority: list[int] = []
        removed_priority: list[int] = []

        while candidate_indices and total_tokens > max_input:
            removable_non_priority = [
                idx for idx in candidate_indices if idx not in mutable_priority
            ]
            if removable_non_priority:
                remove_idx = removable_non_priority[0]
                candidate_indices.remove(remove_idx)
                removed_non_priority.append(remove_idx)
            else:
                remove_idx = candidate_indices[0]
                candidate_indices.remove(remove_idx)
                if remove_idx in mutable_priority:
                    mutable_priority.remove(remove_idx)
                    removed_priority.append(remove_idx)
            total_tokens = await total_tokens_for(candidate_indices)

        if not candidate_indices:
            if removed_priority:
                dropped_tags = [
                    tag_display[norm]
                    for norm, indices in tag_occurrences.items()
                    if indices and indices[-1] in removed_priority
                ]
                self.logger.warning(
                    "Unable to retain tagged history entries within context window; "
                    "dropped=%s",
                    dropped_tags,
                )
            return []

        final_indices = sorted(candidate_indices)

        if base_indices != final_indices or removed_priority or removed_non_priority:
            added = [idx for idx in final_indices if idx not in base_indices]
            removed = [idx for idx in base_indices if idx not in final_indices]
            added_tags = [
                sorted(tags_by_index.get(idx, set()))
                for idx in added
                if idx in tags_by_index
            ]
            flattened_added = sorted({tag for sublist in added_tags for tag in sublist})
            dropped_tags = [
                sorted(tags_by_index.get(idx, set()))
                for idx in removed_priority
                if idx in tags_by_index
            ]
            flattened_dropped = sorted(
                {tag for sublist in dropped_tags for tag in sublist}
            )
            self.logger.debug(
                "Tag-priority trim adjusted slice: base_start=%s final_indices=%s "
                "added=%s removed=%s removed_non_priority=%s added_tags=%s "
                "dropped_priority=%s",
                base_start_idx,
                final_indices,
                added,
                removed,
                removed_non_priority or None,
                flattened_added or None,
                flattened_dropped or None,
            )
            if flattened_added:
                self.logger.debug(
                    "Retained tags after trim: %s",
                    flattened_added,
                )

        if removed_priority:
            dropped_priority_tags = sorted(
                {
                    tag
                    for idx in removed_priority
                    for tag in tags_by_index.get(idx, set())
                }
            )
            if dropped_priority_tags:
                self.logger.warning(
                    "Dropped tagged history entries to satisfy context window: %s",
                    dropped_priority_tags,
                )

        return [history[i] for i in final_indices]

    async def trim_history(
        self,
        history: Sequence[Mapping[str, Any] | dict[str, Any]],
        *,
        params: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ``history`` trimmed to fit within the model context window."""
        return await self.prompt_budget.trim_history(
            history, params=params, context=context
        )

    async def stream_response(
        self,
        entry_id: str,
        history: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[Any, None]:
        self.upstream.ensure_upstream_ready()
        cfg = {**self.default_generation, **(params or {})}
        cfg["stream"] = True

        if messages is None:
            history = history or []
            ctx = context or {}
            try:
                history = await self.prompt_budget.trim_history(
                    history, params=cfg, context=ctx
                )
            except Exception as e:
                self.logger.exception("Failed to trim history")
                yield {"type": "error", "data": f"Prompt error: {e}"}
                return
            try:
                messages = build_entry_messages(history, **ctx)
            except Exception as e:
                self.logger.exception("Failed to build prompt messages")
                yield {"type": "error", "data": f"Prompt error: {e}"}
                return
        prompt_tokens = estimate_entry_messages_tokens(messages)
        self.prompt_budget.diagnostics(
            prompt_tokens=prompt_tokens,
            params=cfg,
            label="entry:stream",
            extra={
                "history_messages": len(history or []) if history is not None else 0,
                "prompt_messages": len(messages or []),
            },
        )
        self._log_prompt(entry_id, messages, cfg)
        payload = self._build_chat_payload(messages, cfg)
        async with self._acquire_slot(entry_id) as _slot_id:
            stream = _ChatStream(self, payload)

            try:
                async with self._track_stream(entry_id, stream.task):
                    async for item in stream:
                        yield item
            finally:
                await stream.aclose()

    async def complete_messages(
        self,
        messages: Sequence[Mapping[str, Any]] | list[dict[str, Any]],
        *,
        params: Mapping[str, Any] | None = None,
    ) -> str:
        """Request a non-streamed chat completion for ``messages``."""

        self.upstream.ensure_upstream_ready()

        cfg = {**self.default_generation, **(params or {})}
        cfg["stream"] = False

        message_list = list(messages)
        prompt_tokens = estimate_entry_messages_tokens(message_list)
        self.prompt_budget.diagnostics(
            prompt_tokens=prompt_tokens,
            params=cfg,
            label="entry:complete",
            extra={"prompt_messages": len(message_list)},
        )

        payload = self._build_chat_payload(message_list, cfg)
        self._log_prompt("complete", message_list, cfg)
        payload.pop("slot_id", None)
        payload.pop("id", None)

        try:
            response = await self._openai.chat.completions.create(**payload)
        except APIStatusError as exc:
            self.upstream.ensure_upstream_ready()
            detail = f"{exc.status_code} {exc.message}".strip()
            self.logger.error("Completion request failed: %s", detail)
            raise RuntimeError(detail) from exc
        except APITimeoutError as exc:
            self.upstream.ensure_upstream_ready()
            raise RuntimeError(f"Timeout: {exc}") from exc
        except APIError as exc:
            self.upstream.ensure_upstream_ready()
            raise RuntimeError(f"API error: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"Unexpected error: {exc}") from exc

        return self._extract_chat_completion_text(response)

    def _build_chat_payload(
        self,
        messages: Sequence[Mapping[str, Any] | dict[str, Any]],
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized: list[dict[str, Any]] = []
        for message in messages:
            entry = dict(message)
            if "content" not in entry and "text" in entry:
                entry["content"] = entry.pop("text")
            normalized.append(entry)

        payload: dict[str, Any] = {
            "messages": normalized,
            "stream": bool(params.get("stream", True)),
        }

        if "n_predict" in params and params["n_predict"] is not None:
            payload["max_tokens"] = params["n_predict"]
        for key in ("temperature", "top_p", "stop", "seed", "model"):
            if key in params and params[key] is not None:
                payload[key] = params[key]
        for key in ("presence_penalty", "frequency_penalty"):
            if key in params and params[key] is not None:
                payload[key] = params[key]
        if "response_format" in params and params["response_format"] is not None:
            response_format = params["response_format"]
            payload["response_format"] = response_format
            if (
                isinstance(response_format, Mapping)
                and response_format.get("type") == "json_schema"
                and isinstance(response_format.get("json_schema"), Mapping)
            ):
                # llama.cpp OpenAI-compat expects top-level json_schema; send via extra_body.
                json_schema = response_format["json_schema"]
                if isinstance(json_schema, Mapping) and "schema" in json_schema:
                    json_schema = json_schema["schema"]
                payload.setdefault("extra_body", {})["json_schema"] = json_schema
                payload.pop("response_format", None)
        if "model" not in payload:
            payload["model"] = settings.get("LLM.chat.model", "local")
        allowlist = set(settings.get("LLM.chat.parameter_allowlist") or [])
        config_params = settings.get("LLM.chat.parameters") or {}
        if isinstance(config_params, Mapping):
            for key, value in config_params.items():
                if key in allowlist:
                    payload.setdefault("extra_body", {})[key] = value
        for key, value in params.items():
            if key in allowlist and value is not None:
                payload.setdefault("extra_body", {})[key] = value

        return payload

    def _log_prompt(
        self,
        entry_id: str | None,
        messages: Sequence[Mapping[str, Any]],
        params: Mapping[str, Any],
    ) -> None:
        logger = logging.getLogger(__name__)
        logger.debug(
            "Prompt %s (entry=%s): %s",
            params.get("model", settings.get("LLM.chat.model", "local")),
            entry_id,
            [
                {
                    "role": msg.get("role"),
                    "content": msg.get("content") or msg.get("text"),
                }
                for msg in messages
            ],
        )

    async def abort(self, entry_id: str) -> bool:
        slot_id: int | None = None
        async with self._streams_lock:
            task = self._active_streams.pop(entry_id, None)
            slot_id = self._active_slots.pop(entry_id, None)
            if slot_id is not None:
                self._slots_released_by_abort.add(entry_id)
        if slot_id is not None:
            self._slot_queue.put_nowait(slot_id)
            self._slot_semaphore.release()
        if task is not None:
            self.logger.info("Aborting stream %s", entry_id)
            try:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            except Exception:
                self.logger.exception("Error closing stream %s", entry_id)
            return True
        if slot_id is not None:
            self.logger.info("Cancelled pending slot for %s", entry_id)
            return True
        self.logger.debug("No active stream to abort for %s", entry_id)
        return False

    @asynccontextmanager
    async def _track_stream(
        self, entry_id: str, task: asyncio.Task[None]
    ) -> AsyncGenerator[None, None]:
        async with self._streams_lock:
            self._active_streams[entry_id] = task
        try:
            yield
        finally:
            async with self._streams_lock:
                current = self._active_streams.get(entry_id)
                if current is task:
                    self._active_streams.pop(entry_id, None)

    @asynccontextmanager
    async def _acquire_slot(self, entry_id: str) -> AsyncGenerator[int, None]:
        await self._slot_semaphore.acquire()
        slot_id: int | None = None
        try:
            slot_id = await self._slot_queue.get()
            async with self._streams_lock:
                self._active_slots[entry_id] = slot_id
            yield slot_id
        finally:
            release_slot = True
            async with self._streams_lock:
                if entry_id in self._slots_released_by_abort:
                    release_slot = False
                    self._slots_released_by_abort.discard(entry_id)
                else:
                    self._active_slots.pop(entry_id, None)
            if slot_id is not None and release_slot:
                self._slot_queue.put_nowait(slot_id)
                self._slot_semaphore.release()
            elif release_slot:
                self._slot_semaphore.release()
