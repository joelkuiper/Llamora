"""Prompt budget management for LLM context windows.

This module provides utilities for computing and managing prompt token
budgets within the LLM context window, including diagnostics for when
limits are reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from llamora.settings import settings

if TYPE_CHECKING:
    from llamora.app.services.service_pulse import ServicePulse
    from llamora.llm.client import LLMClient


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
        """Return True if prompt tokens are at or above the maximum."""
        return self.max_tokens is not None and self.prompt_tokens >= self.max_tokens

    @property
    def exceeded(self) -> bool:
        """Return True if prompt tokens exceed the maximum."""
        return self.max_tokens is not None and self.prompt_tokens > self.max_tokens


class PromptBudget:
    """Helper for computing prompt budgets and reporting usage diagnostics.

    This class manages the token budget for LLM prompts, ensuring that
    messages fit within the model's context window while reserving
    space for generation.
    """

    def __init__(
        self,
        client: "LLMClient",
        *,
        service_pulse: "ServicePulse | None" = None,
    ) -> None:
        self._client = client
        self._service_pulse = service_pulse
        self._logger = client.logger

    def max_prompt_tokens(self, params: Mapping[str, Any] | None = None) -> int | None:
        """Return the maximum tokens available for the prompt portion.

        This accounts for the context size, generation parameters (n_predict),
        and configured safety margins.
        """
        ctx_size = self._client.ctx_size
        if ctx_size is None:
            return None
        ctx_size = self._apply_safety_margin(ctx_size)

        cfg: dict[str, Any] = dict(self._client.default_generation)
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

    @staticmethod
    def _apply_safety_margin(ctx_size: int) -> int:
        """Apply configured safety margin to the context size."""
        cfg = settings.get("LLM.tokenizer.safety_margin") or {}
        try:
            ratio = float(cfg.get("ratio", 0.0))
        except (TypeError, ValueError):
            ratio = 0.0
        try:
            min_tokens = int(cfg.get("min_tokens", 0))
        except (TypeError, ValueError):
            min_tokens = 0
        try:
            max_tokens = int(cfg.get("max_tokens", 0))
        except (TypeError, ValueError):
            max_tokens = 0

        margin = int(ctx_size * ratio) if ratio > 0 else 0
        if min_tokens > 0:
            margin = max(margin, min_tokens)
        if max_tokens > 0:
            margin = min(margin, max_tokens)

        if margin <= 0:
            return ctx_size
        return max(ctx_size - margin, 0)

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


__all__ = [
    "PromptBudget",
    "PromptBudgetSnapshot",
]
