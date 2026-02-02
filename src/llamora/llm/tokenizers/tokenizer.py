"""Tokenizer helpers backed by Hugging Face transformers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import Any


from transformers import AutoTokenizer, PreTrainedTokenizerBase

from llamora.settings import settings

__all__ = [
    "count_tokens",
    "get_tokenizer",
    "format_message_fragment",
    "count_message_tokens",
    "history_suffix_token_totals",
]

_TOKENIZER: PreTrainedTokenizerBase | None = None
_TOKENIZER_LOCK = Lock()


def _normalise_model_identifier(raw: Any) -> str:
    """Return a string path or identifier for the tokenizer model."""

    if isinstance(raw, Path):
        return str(raw)
    return str(raw)


def get_tokenizer() -> PreTrainedTokenizerBase:
    """Load and cache the Hugging Face tokenizer defined in the settings."""

    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER

    with _TOKENIZER_LOCK:
        if _TOKENIZER is not None:
            return _TOKENIZER

        config = settings.get("LLM.tokenizer")
        if isinstance(config, str):
            model_id = config
            kwargs: dict[str, Any] = {}
        elif isinstance(config, Mapping):
            cfg_dict = dict(config)
            model_id: Any | None = None
            for key in (
                "model",
                "path",
                "model_path",
                "name",
                "pretrained_model_name_or_path",
            ):
                model_id = cfg_dict.pop(key, None)
                if model_id is not None:
                    break
            if model_id is None:
                raise ValueError(
                    "LLM.tokenizer configuration must define a model identifier"
                )
            kwargs = {
                str(k): _normalise_model_identifier(v) if isinstance(v, Path) else v
                for k, v in cfg_dict.items()
            }
            kwargs.setdefault("trust_remote_code", True)
        else:
            raise ValueError("LLM.tokenizer must be a string or mapping")

        model_name = _normalise_model_identifier(model_id)
        tokenizer = AutoTokenizer.from_pretrained(model_name, **kwargs)
        _TOKENIZER = tokenizer
        return tokenizer


def count_tokens(prompt: str) -> int:
    """Return the number of tokens produced by the configured tokenizer."""

    tokenizer = get_tokenizer()
    encoded = tokenizer.encode(prompt, add_special_tokens=False)
    return len(encoded)


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

