from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager, suppress
from types import MappingProxyType
from typing import Any, AsyncGenerator, Mapping, NamedTuple, Sequence, TYPE_CHECKING

import httpx
import orjson
from httpx import HTTPError

from cachetools import LRUCache

if TYPE_CHECKING:
    from llamora.app.services.service_pulse import ServicePulse
from llamora.app.util import canonicalize
from llamora.settings import settings
from .process_manager import LlamafileProcessManager
from .entry_template import build_entry_messages, render_entry_prompt
from .tokenizers.tokenizer import count_tokens, history_suffix_token_totals

DEFAULT_LLM_REQUEST = dict(settings.LLM.request)
HISTORY_TOKEN_CACHE_SIZE = 32


@dataclass(slots=True, frozen=True)
class PromptBudgetSnapshot:
    """Summary of a prompt's token usage against the available context."""

    prompt_tokens: int
    max_tokens: int | None
    overflow: int
    saturation: float | None
    context_size: int | None
    label: str | None = None
    params: Mapping[str, Any] | None = None
    extra: Mapping[str, Any] | None = None

    @property
    def at_ceiling(self) -> bool:
        return self.max_tokens is not None and self.prompt_tokens >= self.max_tokens

    @property
    def exceeded(self) -> bool:
        return self.max_tokens is not None and self.prompt_tokens > self.max_tokens


class PromptBudget:
    """Helper for computing prompt budgets and reporting usage diagnostics."""

    def __init__(
        self,
        client: LLMClient,
        *,
        service_pulse: ServicePulse | None = None,
    ) -> None:
        self._client = client
        self._service_pulse = service_pulse
        self._logger = client.logger

    def max_prompt_tokens(self, params: Mapping[str, Any] | None = None) -> int | None:
        """Return the maximum tokens available for the prompt portion."""

        ctx_size = self._client.ctx_size
        if ctx_size is None:
            return None

        cfg: dict[str, Any] = dict(self._client.default_request)
        if params:
            for key, value in params.items():
                if value is not None:
                    cfg[key] = value

        n_predict = cfg.get("n_predict")
        if n_predict is None:
            return ctx_size

        try:
            predict_tokens = int(n_predict)
        except (TypeError, ValueError):
            return ctx_size

        return max(ctx_size - predict_tokens, 0)

    async def trim_history(
        self,
        history: Sequence[Mapping[str, Any] | dict[str, Any]],
        *,
        params: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ``history`` trimmed to fit the available prompt budget."""

        history_list = [
            dict(entry) if not isinstance(entry, dict) else entry for entry in history
        ]
        if not history_list:
            return history_list

        max_input = self.max_prompt_tokens(params)
        if max_input is None or max_input <= 0:
            return history_list

        ctx = dict(context or {})
        return await self._client._trim_history(history_list, max_input, ctx)

    def diagnostics(
        self,
        *,
        prompt_tokens: int,
        params: Mapping[str, Any] | None = None,
        label: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> PromptBudgetSnapshot:
        """Analyse prompt usage and emit diagnostics if limits are reached."""

        max_tokens = self.max_prompt_tokens(params)
        overflow = 0
        saturation: float | None = None

        if max_tokens is not None and max_tokens > 0:
            overflow = max(0, prompt_tokens - max_tokens)
            saturation = prompt_tokens / max_tokens

        params_copy = MappingProxyType(dict(params)) if params is not None else None
        extra_copy = MappingProxyType(dict(extra)) if extra is not None else None

        snapshot = PromptBudgetSnapshot(
            prompt_tokens=prompt_tokens,
            max_tokens=max_tokens,
            overflow=overflow,
            saturation=saturation,
            context_size=self._client.ctx_size,
            label=label,
            params=params_copy,
            extra=extra_copy,
        )

        if snapshot.at_ceiling:
            label_suffix = f" ({snapshot.label})" if snapshot.label else ""
            self._logger.warning(
                "Prompt budget ceiling reached%s: tokens=%s max=%s overflow=%s",
                label_suffix,
                snapshot.prompt_tokens,
                snapshot.max_tokens,
                snapshot.overflow,
            )
            if self._service_pulse is not None:
                payload: dict[str, Any] = {
                    "label": snapshot.label,
                    "prompt_tokens": snapshot.prompt_tokens,
                    "max_tokens": snapshot.max_tokens,
                    "overflow": snapshot.overflow,
                    "saturation": snapshot.saturation,
                    "context_size": snapshot.context_size,
                }
                if snapshot.extra is not None:
                    payload["extra"] = dict(snapshot.extra)
                if snapshot.params is not None:
                    payload["params"] = dict(snapshot.params)
                try:
                    self._service_pulse.emit("llm.prompt_budget", payload)
                except Exception:  # pragma: no cover - defensive
                    self._logger.exception("Failed to emit prompt budget pulse")

        return snapshot


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
        except httpx.HTTPStatusError as e:
            response = e.response
            status_line = str(e)
            body: bytes | None = None
            if response is not None:
                status_line = (
                    f"{response.status_code} {response.reason_phrase or ''}".strip()
                )
                try:
                    body = await response.aread()
                except Exception:
                    body = None
            detail = self._client._normalize_response_detail(body, status_line)
            self._client.logger.error(
                "Completion request failed (%s): %s", status_line, detail
            )
            self._client.process_manager.ensure_server_running()
            await self._emit({"type": "error", "data": detail})
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
        process_manager: LlamafileProcessManager,
        default_request: dict | None = None,
        *,
        service_pulse: ServicePulse | None = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.process_manager = process_manager
        self.default_request = {**DEFAULT_LLM_REQUEST, **(default_request or {})}
        self.ctx_size = process_manager.ctx_size
        self.server_props = process_manager.server_props
        self._client = httpx.AsyncClient(
            timeout=None, transport=httpx.AsyncHTTPTransport(retries=0)
        )
        self._active_streams: dict[str, asyncio.Task[None]] = {}
        self._streams_lock = asyncio.Lock()
        self._active_slots: dict[str, int] = {}
        self._slots_released_by_abort: set[str] = set()
        self.parallel_slots = max(1, getattr(process_manager, "parallel_slots", 1))
        self._slot_semaphore = asyncio.Semaphore(self.parallel_slots)
        self._slot_queue: asyncio.LifoQueue[int] = asyncio.LifoQueue()
        for slot_id in range(self.parallel_slots):
            self._slot_queue.put_nowait(slot_id)
        self._sse_headers = {
            "Accept": "text/event-stream",
            "Connection": "keep-alive",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        }
        self.prompt_budget = PromptBudget(self, service_pulse=service_pulse)
        # Cached cumulative token counts keyed by (history_hash, context_hash).
        # The cache lets adjacent requests within the same stream reuse
        # tokenisation results instead of repeatedly calling the HTTP endpoint.
        self._history_token_cache: LRUCache[tuple[str, str], tuple[int, ...]] = (
            LRUCache(maxsize=HISTORY_TOKEN_CACHE_SIZE)
        )

    @staticmethod
    def _normalize_response_detail(
        body: bytes | str | None,
        fallback: str,
    ) -> str:
        """Derive a human-readable error message from an HTTP response body."""

        fallback = fallback.strip() or "HTTP error"
        if body is None:
            return fallback

        if isinstance(body, (bytes, bytearray)):
            text = body.decode("utf-8", "replace")
        else:
            text = str(body)

        text = text.strip()
        if not text:
            return fallback

        try:
            payload = orjson.loads(text)
        except Exception:
            return text

        if isinstance(payload, dict):
            for key in ("detail", "message", "error"):
                if key in payload:
                    detail = LLMClient._stringify_detail(payload[key])
                    if detail:
                        return detail
            detail = LLMClient._stringify_detail(payload)
            if detail:
                return detail
            return fallback

        detail = LLMClient._stringify_detail(payload)
        return detail or fallback

    @staticmethod
    def _stringify_detail(value: Any) -> str | None:
        """Convert nested JSON detail values into a readable string."""

        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        if isinstance(value, (bytes, bytearray)):
            decoded = value.decode("utf-8", "replace").strip()
            return decoded or None
        if isinstance(value, (list, tuple, set)):
            parts = [
                part
                for part in (LLMClient._stringify_detail(item) for item in value)
                if part
            ]
            if parts:
                return ", ".join(parts)
            return None
        if isinstance(value, dict):
            try:
                return orjson.dumps(value).decode()
            except Exception:
                return str(value)
        return str(value).strip() or None

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
        return await asyncio.to_thread(count_tokens, text)

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
                keywords = meta.get("keywords")
                if isinstance(keywords, Sequence) and not isinstance(
                    keywords, (str, bytes)
                ):
                    for keyword in keywords:
                        canonical_keyword = self._canonicalize_tag_value(keyword)
                        if canonical_keyword:
                            canonical_tags.add(canonical_keyword)

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
        ``meta.keywords``), the function reintroduces the most recent instance
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
        msg_id: str,
        history: list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[Any, None]:
        self.process_manager.ensure_server_running()

        cfg = {**self.default_request, **(params or {})}

        prompt_text: str | None = None

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
        try:
            prompt_render = render_entry_prompt(messages)
        except Exception as e:
            self.logger.exception("Failed to render entry prompt")
            yield {"type": "error", "data": f"Prompt error: {e}"}
            return

        self.prompt_budget.diagnostics(
            prompt_tokens=prompt_render.token_count,
            params=cfg,
            label="entry:stream",
            extra={
                "history_messages": len(history or []) if history is not None else 0,
                "prompt_messages": len(messages or []),
            },
        )

        prompt_text = prompt_render.prompt

        payload = {"prompt": prompt_text, **cfg}
        if msg_id:
            payload.setdefault("id", str(msg_id))

        async with self._acquire_slot(msg_id) as slot_id:
            payload.setdefault("slot_id", slot_id)
            stream = _CompletionStream(self, payload)

            try:
                async with self._track_stream(msg_id, stream.task):
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
        """Request a non-streamed completion for ``messages``."""

        self.process_manager.ensure_server_running()

        cfg = {**self.default_request, **(params or {})}
        cfg["stream"] = False

        message_list = list(messages)

        try:
            prompt_render = render_entry_prompt(message_list)
        except Exception as exc:
            self.logger.exception("Failed to render completion prompt")
            raise RuntimeError("Prompt rendering failed") from exc

        self.prompt_budget.diagnostics(
            prompt_tokens=prompt_render.token_count,
            params=cfg,
            label="entry:complete",
            extra={"prompt_messages": len(message_list)},
        )

        prompt_text = prompt_render.prompt

        payload = {"prompt": prompt_text, **cfg}
        payload.pop("slot_id", None)
        payload.pop("id", None)

        try:
            response = await self._client.post(
                f"{self.server_url}/completion",
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            http_response = exc.response
            status_line = str(exc)
            body: bytes | None = None
            if http_response is not None:
                status_line = (
                    f"{http_response.status_code} {http_response.reason_phrase or ''}"
                ).strip()
                try:
                    body = await http_response.aread()
                except Exception:
                    body = None
            detail = self._normalize_response_detail(body, status_line)
            self.logger.error("Completion request failed (%s): %s", status_line, detail)
            self.process_manager.ensure_server_running()
            raise RuntimeError(detail) from exc
        except HTTPError as exc:
            self.process_manager.ensure_server_running()
            raise RuntimeError(f"HTTP error: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"Unexpected error: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            return response.text.strip()

        text = self._extract_completion_text(payload)
        return text.strip()

    def _extract_completion_text(self, payload: Any) -> str:
        """Normalise completion responses into a string."""

        if isinstance(payload, Mapping):
            for key in ("content", "completion", "text", "data"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
            choices = payload.get("choices")
            if choices is not None:
                text = self._extract_completion_text(choices)
                if text:
                    return text
            message = payload.get("message")
            if message is not None:
                text = self._extract_completion_text(message)
                if text:
                    return text
            results = payload.get("results")
            if results is not None:
                text = self._extract_completion_text(results)
                if text:
                    return text
            return ""

        if isinstance(payload, Sequence) and not isinstance(
            payload, (str, bytes, bytearray)
        ):
            parts = [self._extract_completion_text(item) for item in payload]
            return "".join(part for part in parts if part)

        if isinstance(payload, (bytes, bytearray)):
            return payload.decode("utf-8", "replace")

        return str(payload or "")

    async def abort(self, msg_id: str) -> bool:
        slot_id: int | None = None
        async with self._streams_lock:
            task = self._active_streams.pop(msg_id, None)
            slot_id = self._active_slots.pop(msg_id, None)
            if slot_id is not None:
                self._slots_released_by_abort.add(msg_id)
        if slot_id is not None:
            self._slot_queue.put_nowait(slot_id)
            self._slot_semaphore.release()
        if task is not None:
            self.logger.info("Aborting stream %s", msg_id)
            try:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            except Exception:
                self.logger.exception("Error closing stream %s", msg_id)
            return True
        if slot_id is not None:
            self.logger.info("Cancelled pending slot for %s", msg_id)
            return True
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

    @asynccontextmanager
    async def _acquire_slot(self, msg_id: str) -> AsyncGenerator[int, None]:
        await self._slot_semaphore.acquire()
        slot_id: int | None = None
        try:
            slot_id = await self._slot_queue.get()
            async with self._streams_lock:
                self._active_slots[msg_id] = slot_id
            yield slot_id
        finally:
            release_slot = True
            async with self._streams_lock:
                if msg_id in self._slots_released_by_abort:
                    release_slot = False
                    self._slots_released_by_abort.discard(msg_id)
                else:
                    self._active_slots.pop(msg_id, None)
            if slot_id is not None and release_slot:
                self._slot_queue.put_nowait(slot_id)
                self._slot_semaphore.release()
            elif release_slot:
                self._slot_semaphore.release()
