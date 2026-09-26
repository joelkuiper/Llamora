from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from llamora.llm.endpoints import resolve_endpoints
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


class UpstreamProcessManager:
    """Track a remote OpenAI-compatible upstream endpoint."""

    def __init__(self, upstream_args: dict | None = None) -> None:
        self.logger = logging.getLogger(__name__)

        raw_upstream_cfg = settings.get("LLM.upstream") or {}
        upstream_cfg = _normalise_arg_keys(_to_plain_dict(raw_upstream_cfg))
        upstream_cfg.update(_normalise_arg_keys(_to_plain_dict(upstream_args)))

        # One interpretation of host/base_url/"/v1" for everything (see endpoints.py).
        self.endpoints = resolve_endpoints(
            host=upstream_cfg.get("host"),
            base_url=settings.get("LLM.chat.base_url"),
            endpoint=settings.get("LLM.chat.endpoint"),
        )
        self.upstream_url = self.endpoints.root
        self._ctx_size = upstream_cfg.get("ctx_size")
        self._upstream_props: dict[str, Any] | None = None
        configured_parallel = upstream_cfg.get("parallel")
        self._parallel_slots = _coerce_parallel(configured_parallel, default=1)
        self._health_ttl = float(upstream_cfg.get("health_ttl", 10.0))
        self._skip_health_check = bool(upstream_cfg.get("skip_health_check", False))
        self._last_healthy: float = 0.0
        # /health and /props are llama.cpp extensions. Hosted OpenAI-compatible
        # APIs usually lack them; once an endpoint turns out to be missing it
        # is not probed again.
        self._health_supported = True
        self._props_supported = True
        # Keyed servers (e.g. llama-server --api-key) protect /props and
        # sometimes /health too, so probes send the same key as chat requests.
        api_key = str(settings.get("LLM.chat.api_key") or "").strip()
        self._headers: dict[str, str] = (
            {"Authorization": f"Bearer {api_key}"} if api_key else {}
        )

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
        if self._needs_metadata():
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
        if self._needs_metadata():
            await self._async_refresh_upstream_metadata()

    def shutdown(self) -> None:
        """No-op for remote upstreams."""

        return None

    def _needs_metadata(self) -> bool:
        return self._props_supported and (
            self._upstream_props is None or self._ctx_size is None
        )

    def _health_verdict(self, status: int) -> bool:
        """200 is healthy and 5xx is not (llama.cpp answers 503 while loading).

        Anything else means the server answered but has no /health endpoint:
        it is reachable, so treat it as healthy and stop probing it.
        """
        if status == 200:
            return True
        if status >= 500:
            return False
        if self._health_supported:
            self.logger.info(
                "Upstream has no /health endpoint (status %s); skipping health checks",
                status,
            )
        self._health_supported = False
        return True

    def _props_payload(self, resp: httpx.Response) -> Mapping | None:
        if resp.status_code != 200:
            if resp.status_code < 500:
                # No /props (not llama.cpp): keep the configured defaults.
                self._props_supported = False
            self.logger.debug(
                "Failed to fetch upstream props (status %s)", resp.status_code
            )
            return None
        try:
            data = resp.json()
        except Exception:
            self.logger.debug("Failed to parse upstream props", exc_info=True)
            return None
        return data if isinstance(data, Mapping) else None

    def _is_upstream_healthy(self) -> bool:
        if not self._health_supported:
            return True
        try:
            resp = httpx.get(
                f"{self.upstream_url}/health", headers=self._headers, timeout=1.0
            )
        except Exception:
            return False
        return self._health_verdict(resp.status_code)

    def _refresh_upstream_metadata(self) -> None:
        try:
            resp = httpx.get(
                f"{self.upstream_url}/props", headers=self._headers, timeout=2.0
            )
        except Exception:
            self.logger.debug("Failed to fetch upstream props", exc_info=True)
            return
        data = self._props_payload(resp)
        if data is not None:
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
        if not self._health_supported:
            return True
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.upstream_url}/health", headers=self._headers, timeout=1.0
                )
        except Exception:
            return False
        return self._health_verdict(resp.status_code)

    async def _async_refresh_upstream_metadata(self) -> None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.upstream_url}/props", headers=self._headers, timeout=2.0
                )
        except Exception:
            self.logger.debug("Failed to fetch upstream props", exc_info=True)
            return
        data = self._props_payload(resp)
        if data is not None:
            self._apply_props(data)
