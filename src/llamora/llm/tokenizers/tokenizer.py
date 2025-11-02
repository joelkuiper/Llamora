"""Tokenizer helpers backed by Hugging Face transformers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from transformers import AutoTokenizer, PreTrainedTokenizerBase

from llamora.settings import settings

__all__ = [
    "count_tokens",
    "format_message_fragment",
    "count_message_tokens",
    "format_vibes_text",
    "history_suffix_token_totals",
]

_TOKENIZER: PreTrainedTokenizerBase | None = None
_TOKENIZER_LOCK = Lock()


def _normalise_model_identifier(raw: Any) -> str:
    """Return a string path or identifier for the tokenizer model."""

    if isinstance(raw, Path):
        return str(raw)
    return str(raw)


def _load_tokenizer() -> PreTrainedTokenizerBase:
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
            kwargs = {str(k): _normalise_model_identifier(v) if isinstance(v, Path) else v for k, v in cfg_dict.items()}
            kwargs.setdefault("trust_remote_code", True)
        else:
            raise ValueError("LLM.tokenizer must be a string or mapping")

        model_name = _normalise_model_identifier(model_id)
        tokenizer = AutoTokenizer.from_pretrained(model_name, **kwargs)
        _TOKENIZER = tokenizer
        return tokenizer


def count_tokens(prompt: str) -> int:
    """Return the number of tokens produced by the configured tokenizer."""

    tokenizer = _load_tokenizer()
    encoded = tokenizer.encode(prompt, add_special_tokens=False)
    return len(encoded)


def format_message_fragment(role: str, message: str) -> str:
    """Render a history entry in the format used by the chat prompt template."""

    safe_role = (role or "user").strip() or "user"
    safe_message = (message or "").strip()
    return f"<|im_start|>{safe_role}\n{safe_message}<|im_end|>\n"


def count_message_tokens(role: str, message: str) -> int:
    """Return the token count for a single history entry."""

    fragment = format_message_fragment(role, message)
    return count_tokens(fragment)


def _coerce_mapping(entry: Mapping[str, Any] | dict[str, Any]) -> Mapping[str, Any]:
    return entry if isinstance(entry, Mapping) else dict(entry)


def _extract_emoji(entry: Mapping[str, Any] | dict[str, Any]) -> str | None:
    mapping = _coerce_mapping(entry)
    meta = mapping.get("meta")
    if not isinstance(meta, Mapping):
        return None
    emoji = meta.get("emoji")
    if not emoji:
        return None
    return str(emoji)


def _format_vibes_line(display_emojis: Sequence[str]) -> str:
    if not display_emojis:
        return ""
    joined = " ".join(display_emojis)
    return f"Emoji vibes for this conversation: {joined}\n"


@lru_cache(maxsize=256)
def _count_vibes_tokens(display_emojis: tuple[str, ...]) -> int:
    if not display_emojis:
        return 0
    text = _format_vibes_line(display_emojis)
    return count_tokens(text)


def format_vibes_text(history: Sequence[Mapping[str, Any] | dict[str, Any]]) -> str:
    """Render the optional emoji vibes line for ``history``.

    The chat prompt template displays at most the five most recent emojis,
    ordered from newest to oldest, when any history entries include an
    ``emoji`` value in their ``meta`` mapping.
    """

    emojis: list[str] = []
    for entry in history:
        emoji = _extract_emoji(entry)
        if emoji:
            emojis.append(emoji)

    display = tuple(reversed(emojis[-5:]))
    return _format_vibes_line(display)


def history_suffix_token_totals(
    history: Sequence[Mapping[str, Any] | dict[str, Any]]
) -> list[int]:
    """Return cumulative token totals for each history suffix.

    Each element ``i`` corresponds to the number of tokens contributed by
    ``history[i:]`` when rendered inside the chat prompt template. Counts
    include both the message fragments and the optional emoji vibes line.
    """

    if not history:
        return []

    totals = [0] * len(history)
    running = 0
    display_emojis: list[str] = []

    for offset in range(len(history) - 1, -1, -1):
        raw_entry = history[offset]
        entry = _coerce_mapping(raw_entry)
        entry_tokens = int(entry.get("prompt_tokens") or 0)
        running += max(entry_tokens, 0)

        emoji = _extract_emoji(entry)
        if emoji and len(display_emojis) < 5:
            display_emojis.append(emoji)

        vibe_tokens = _count_vibes_tokens(tuple(display_emojis))
        totals[offset] = running + vibe_tokens

    return totals
