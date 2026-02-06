"""Token counting helpers backed by tiktoken."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import tiktoken

from llamora.settings import settings

__all__ = [
    "count_tokens",
    "estimate_tokens",
    "format_message_fragment",
    "count_message_tokens",
    "history_suffix_token_totals",
]


def estimate_tokens(prompt: str) -> int:
    """Return token count for ``prompt`` using tiktoken."""

    if not prompt:
        return 0
    encoding_name = settings.get("LLM.tokenizer.encoding", "cl100k_base")
    encoding = tiktoken.get_encoding(str(encoding_name))
    return len(encoding.encode(prompt))


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

    history = ({"role": role, "text": message},)
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
