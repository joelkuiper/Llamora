"""Helpers for validating runtime configuration."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable

from llamora.settings import settings


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


def _validate_llm_server() -> Iterable[str]:
    server = settings.get("LLM.server")
    host = _normalise_text(_get_value(server, "host"))
    llamafile_path = _normalise_text(_get_value(server, "llamafile_path"))

    if not host and not llamafile_path:
        yield (
            "Configure the LLM server by setting either LLAMORA_LLM__SERVER__HOST "
            "for an OpenAI-compatible server or LLAMORA_LLM__SERVER__LLAMAFILE_PATH "
            "for a local llamafile."
        )


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
    errors.extend(_validate_llm_server())
    errors.extend(_validate_secrets())
    return errors


__all__ = ["validate_settings"]
