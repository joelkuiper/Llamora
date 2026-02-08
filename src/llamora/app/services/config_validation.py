"""Helpers for validating runtime configuration."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable

from llamora.settings import settings
from llamora.app.util.number import coerce_float, coerce_int


def _normalise_text(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _get_value(mapping: object, name: str) -> object | None:
    if mapping is None:
        return None
    if hasattr(mapping, name):
        return getattr(mapping, name)
    getter = getattr(mapping, "get", None)
    if callable(getter):
        return getter(name)
    return None


def _validate_llm_upstream() -> Iterable[str]:
    upstream = settings.get("LLM.upstream")
    host = _normalise_text(_get_value(upstream, "host"))
    llamafile_path = _normalise_text(_get_value(upstream, "llamafile_path"))

    if not host and not llamafile_path:
        yield (
            "Configure an OpenAI-compatible upstream by setting either "
            "LLAMORA_LLM__UPSTREAM__HOST for a remote endpoint or "
            "LLAMORA_LLM__UPSTREAM__LLAMAFILE_PATH for a local llamafile."
        )


def _validate_llm_chat_settings() -> Iterable[str]:
    timeout = _get_value(settings, "LLM.chat.timeout_seconds")
    retries = _get_value(settings, "LLM.chat.max_retries")

    if timeout is not None:
        timeout_value = coerce_float(timeout)
        if timeout_value is None:
            yield "LLM.chat.timeout_seconds must be a number."
        else:
            if timeout_value <= 0 or timeout_value > 300:
                yield "LLM.chat.timeout_seconds must be between 1 and 300 seconds."

    if retries is not None:
        retries_value = coerce_int(retries)
        if retries_value is None:
            yield "LLM.chat.max_retries must be an integer."
        else:
            if retries_value < 0 or retries_value > 10:
                yield "LLM.chat.max_retries must be between 0 and 10."


def _validate_secrets() -> Iterable[str]:
    secret_key = _normalise_text(settings.get("SECRET_KEY"))
    if not secret_key:
        yield "Set LLAMORA_SECRET_KEY (or SECRET_KEY) to a strong, non-empty value."

    cookie_secret = _normalise_text(settings.get("COOKIES.secret"))
    if not cookie_secret:
        yield "Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string."
        return

    try:
        decoded = base64.b64decode(cookie_secret, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        yield "Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string."
        return

    if len(decoded) != 32:
        yield "Set LLAMORA_COOKIE_SECRET to a 32-byte base64 string."


def validate_settings() -> list[str]:
    """Return a list of configuration validation error messages."""

    errors: list[str] = []
    errors.extend(_validate_llm_upstream())
    errors.extend(_validate_llm_chat_settings())
    errors.extend(_validate_secrets())
    return errors


__all__ = ["validate_settings"]
