from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from llamora.settings import settings


def _to_plain_dict(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    if isinstance(data, Mapping):
        return dict(data)
    return {}


def _normalise_arg_keys(args: dict[str, Any]) -> dict[str, Any]:
    normalised: dict[str, Any] = {}
    for key, value in args.items():
        key_str = str(key).replace("-", "_").lower()
        normalised[key_str] = value
    return normalised


def _coerce_parallel(value: Any, default: int = 1) -> int:
    try:
        if value is None:
            raise ValueError
        slots = int(value)
    except (TypeError, ValueError):
        return max(default, 1)
    return max(slots, 1)


def _strip_base_url(raw: str) -> str:
    text = str(raw or "").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/v1"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            text = text.rstrip("/")
    return text


class UpstreamProcessManager:
    """Track a remote OpenAI-compatible upstream endpoint."""

    def __init__(self, upstream_args: dict | None = None) -> None:
        self.logger = logging.getLogger(__name__)

        raw_upstream_cfg = settings.get("LLM.upstream") or {}
        upstream_cfg = _normalise_arg_keys(_to_plain_dict(raw_upstream_cfg))
        upstream_cfg.update(_normalise_arg_keys(_to_plain_dict(upstream_args)))

        host = upstream_cfg.get("host")
        if not host:
            base_url = settings.get("LLM.chat.base_url")
            if base_url:
                host = _strip_base_url(str(base_url))

        if not host:
            raise ValueError(
                "Configure settings.LLM.upstream.host or set LLAMORA_LLM__UPSTREAM__HOST"
            )

        self.upstream_url = str(host).rstrip("/")
        self._ctx_size = upstream_cfg.get("ctx_size")
        self._upstream_props: dict[str, Any] | None = None
        configured_parallel = upstream_cfg.get("parallel")
        self._parallel_slots = _coerce_parallel(configured_parallel, default=1)
        self._health_ttl = float(upstream_cfg.get("health_ttl", 10.0))
        self._skip_health_check = bool(upstream_cfg.get("skip_health_check", False))
        self._last_healthy: float = 0.0

    @property
    def ctx_size(self) -> int | None:
        return self._ctx_size

    @property
    def upstream_props(self) -> dict[str, Any] | None:
        return self._upstream_props

    def base_url(self) -> str:
        return self.upstream_url

    @property
    def parallel_slots(self) -> int:
        return self._parallel_slots

    def ensure_upstream_ready(self) -> None:
        if self._skip_health_check:
            return
        if not self._is_upstream_healthy():
            raise RuntimeError("LLM upstream is unavailable")
        if self._upstream_props is None or self._ctx_size is None:
            self._refresh_upstream_metadata()

    async def async_ensure_upstream_ready(self) -> None:
        """Non-blocking variant of :meth:`ensure_upstream_ready`.

        Skips the health check if the upstream was healthy within the last
        ``health_ttl`` seconds, or if ``skip_health_check`` is enabled.
        """
        if self._skip_health_check:
            return
        now = time.monotonic()
        if (now - self._last_healthy) < self._health_ttl:
            return
        if not await self._async_is_upstream_healthy():
            raise RuntimeError("LLM upstream is unavailable")
        self._last_healthy = now
        if self._upstream_props is None or self._ctx_size is None:
            await self._async_refresh_upstream_metadata()

    def shutdown(self) -> None:
        """No-op for remote upstreams."""

        return None

    def _is_upstream_healthy(self) -> bool:
        try:
            resp = httpx.get(f"{self.upstream_url}/health", timeout=1.0)
        except Exception:
            return False
        return resp.status_code == 200

    def _refresh_upstream_metadata(self) -> None:
        try:
            resp = httpx.get(f"{self.upstream_url}/props", timeout=2.0)
        except Exception:
            self.logger.debug("Failed to fetch upstream props", exc_info=True)
            return

        if resp.status_code != 200:
            self.logger.debug(
                "Failed to fetch upstream props (status %s)", resp.status_code
            )
            return

        try:
            data = resp.json()
        except Exception:
            self.logger.debug("Failed to parse upstream props", exc_info=True)
            return

        if not isinstance(data, Mapping):
            return

        self._apply_props(data)

    def _apply_props(self, data: Mapping) -> None:
        """Apply parsed upstream props (shared by sync and async paths)."""
        self._upstream_props = dict(data)
        ctx_raw = data.get("ctx_size") or data.get("n_ctx")
        if ctx_raw is not None:
            try:
                self._ctx_size = int(ctx_raw)
            except (TypeError, ValueError):
                self.logger.debug(
                    "Ignoring invalid ctx size from upstream props", exc_info=True
                )

        slots_raw = data.get("total_slots")
        if slots_raw is not None:
            try:
                self._parallel_slots = _coerce_parallel(
                    slots_raw, default=self._parallel_slots
                )
            except Exception:
                self.logger.debug(
                    "Ignoring invalid total_slots from upstream props", exc_info=True
                )

    async def _async_is_upstream_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.upstream_url}/health", timeout=1.0)
        except Exception:
            return False
        return resp.status_code == 200

    async def _async_refresh_upstream_metadata(self) -> None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.upstream_url}/props", timeout=2.0)
        except Exception:
            self.logger.debug("Failed to fetch upstream props", exc_info=True)
            return

        if resp.status_code != 200:
            self.logger.debug(
                "Failed to fetch upstream props (status %s)", resp.status_code
            )
            return

        try:
            data = resp.json()
        except Exception:
            self.logger.debug("Failed to parse upstream props", exc_info=True)
            return

        if not isinstance(data, Mapping):
            return

        self._apply_props(data)
