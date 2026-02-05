"""Token estimation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from llamora.settings import settings

__all__ = [
    "count_tokens",
    "estimate_tokens",
    "format_message_fragment",
    "count_message_tokens",
    "history_suffix_token_totals",
]


def _heuristic_token_estimate(prompt: str) -> int:
    cfg = settings.get("LLM.tokenizer.estimate") or {}
    chars_per_token = float(cfg.get("chars_per_token", 3.8))
    non_ascii_per_token = float(cfg.get("non_ascii_chars_per_token", 1.0))

    if not prompt:
        return 0

    ascii_count = sum(1 for ch in prompt if ord(ch) <= 0x7F)
    non_ascii_count = len(prompt) - ascii_count

    ascii_tokens = ascii_count / max(chars_per_token, 0.25)
    non_ascii_tokens = non_ascii_count / max(non_ascii_per_token, 0.25)
    estimate = int(ascii_tokens + non_ascii_tokens)
    if ascii_tokens + non_ascii_tokens > estimate:
        estimate += 1
    return max(estimate, 1)


def _apply_estimate_multiplier(tokens: int) -> int:
    cfg = settings.get("LLM.tokenizer.estimate") or {}
    try:
        multiplier = float(cfg.get("multiplier", 1.0))
    except (TypeError, ValueError):
        multiplier = 1.0
    if multiplier <= 0:
        multiplier = 1.0
    boosted = tokens * multiplier
    adjusted = int(boosted)
    if boosted > adjusted:
        adjusted += 1
    return max(adjusted, 1)


def _skimtoken_estimate(prompt: str) -> int:
    if not prompt:
        return 0
    try:
        from skimtoken.multilingual_simple import estimate_tokens as skim_estimate
    except Exception:
        return _heuristic_token_estimate(prompt)

    try:
        skim_count = int(skim_estimate(prompt))
    except Exception:
        return _heuristic_token_estimate(prompt)

    base = max(skim_count, _heuristic_token_estimate(prompt))
    return _apply_estimate_multiplier(base)


def estimate_tokens(prompt: str) -> int:
    """Return a conservative token estimate for ``prompt``."""

    return _skimtoken_estimate(prompt)


def count_tokens(prompt: str) -> int:
    """Return the configured estimate for the number of tokens in ``prompt``."""

    return estimate_tokens(prompt)


def format_message_fragment(role: str, message: str) -> str:
    """Render a history entry in the format used by the entry prompt template."""

    safe_role = (role or "user").strip() or "user"
    safe_message = (message or "").strip()
    return f"<|im_start|>{safe_role}\n{safe_message}<|im_end|>\n"


def count_message_tokens(role: str, message: str) -> int:
    """Return the token count for a single history entry."""

    from llamora.llm.entry_template import render_entry_prompt_series

    history = ({"role": role, "message": message},)
    series = render_entry_prompt_series(history)
    totals = series.suffix_token_counts
    if not totals:
        return 0
    return max(0, totals[0] - series.base_token_count)


def history_suffix_token_totals(
    history: Sequence[Mapping[str, Any] | dict[str, Any]],
    *,
    context: Mapping[str, Any] | None = None,
    **context_kwargs: Any,
) -> tuple[int, ...]:
    """Return cumulative token totals for each suffix of ``history``."""

    from llamora.llm.entry_template import render_entry_prompt_series

    ctx: dict[str, Any] = {}
    if context:
        ctx.update(context)
    if context_kwargs:
        ctx.update(context_kwargs)

    series = render_entry_prompt_series(history, **ctx)
    return series.suffix_token_counts
