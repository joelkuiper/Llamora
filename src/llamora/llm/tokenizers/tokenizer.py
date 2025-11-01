"""Tokenizer helpers backed by Hugging Face transformers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Any

from transformers import AutoTokenizer, PreTrainedTokenizerBase

from llamora.settings import settings

__all__ = ["count_tokens"]

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
