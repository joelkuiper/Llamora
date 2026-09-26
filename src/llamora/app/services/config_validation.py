"""Helpers for validating runtime configuration."""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Iterable
from pathlib import Path

from llamora.settings import settings
from llamora.app.util.number import coerce_float, coerce_int
from llamora.llm.endpoints import resolve_endpoints
from llamora.llm.vision import parse_vision_mode


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
    try:
        resolve_endpoints(
            host=_normalise_text(_get_value(upstream, "host")),
            base_url=_normalise_text(settings.get("LLM.chat.base_url")),
            endpoint=_normalise_text(settings.get("LLM.chat.endpoint")),
        )
    except ValueError as exc:
        yield str(exc)


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


def _validate_llm_summary_settings() -> Iterable[str]:
    timeout = _get_value(settings, "LLM.summary.timeout_seconds")
    if timeout is None:
        return
    timeout_value = coerce_float(timeout)
    if timeout_value is None:
        yield "LLM.summary.timeout_seconds must be a number."
        return
    if timeout_value <= 0 or timeout_value > 300:
        yield "LLM.summary.timeout_seconds must be between 1 and 300 seconds."


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


def _validate_session_settings() -> Iterable[str]:
    idle_raw = _get_value(settings, "SESSION.idle_ttl")
    idle_ttl = coerce_int(idle_raw)
    if idle_ttl is None or idle_ttl <= 0:
        yield "SESSION.idle_ttl must be a positive integer (seconds)."
        idle_ttl = None

    touch_raw = _get_value(settings, "SESSION.cookie_touch_interval")
    touch_interval = coerce_int(touch_raw)
    if touch_interval is None or touch_interval < 0:
        yield "SESSION.cookie_touch_interval must be a non-negative integer (seconds)."
        touch_interval = None

    csrf_raw = _get_value(settings, "SESSION.csrf_ttl")
    csrf_ttl = coerce_int(csrf_raw)
    if csrf_ttl is None or csrf_ttl <= 0:
        yield "SESSION.csrf_ttl must be a positive integer (seconds)."

    if (
        idle_ttl is not None
        and touch_interval is not None
        and touch_interval > idle_ttl
    ):
        yield "SESSION.cookie_touch_interval must not exceed SESSION.idle_ttl."


# WebP (every stored image variant) cannot be larger than this on either side.
_WEBP_MAX_EDGE = 16383


def _validate_images() -> Iterable[str]:
    images = settings.get("IMAGES") or {}

    raw_path = _normalise_text(_get_value(images, "path"))
    if not raw_path:
        yield "IMAGES.path must name a directory for encrypted image files."
    else:
        path = Path(raw_path).expanduser().resolve()
        if path.exists() and not path.is_dir():
            yield f"IMAGES.path ({path}) exists but is not a directory."
        else:
            # The directory is created on startup: its nearest existing
            # ancestor must be writable.
            existing = path
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            if not os.access(existing, os.W_OK | os.X_OK):
                yield f"IMAGES.path ({path}) is not writable."

    limits: tuple[tuple[str, int, int | None], ...] = (
        ("max_upload_bytes", 1024, None),
        ("max_pixels", 1, None),
        ("max_per_entry", 1, 100),
        ("quality", 1, 100),
        ("processing_concurrency", 1, 64),
        ("pending_ttl", 60, None),
        ("sweep_interval", 60, None),
    )
    for name, low, high in limits:
        value = coerce_int(_get_value(images, name))
        if value is None or value < low or (high is not None and value > high):
            bounds = f"between {low} and {high}" if high else f"at least {low}"
            yield f"IMAGES.{name} must be an integer {bounds}."

    sizes = _get_value(images, "sizes") or {}
    edges: list[int] = []
    for variant in ("thumb", "display", "full"):
        edge = coerce_int(_get_value(sizes, variant))
        if edge is None or not 1 <= edge <= _WEBP_MAX_EDGE:
            yield (
                f"IMAGES.sizes.{variant} must be an integer between 1 and "
                f"{_WEBP_MAX_EDGE} (pixels)."
            )
            return
        edges.append(edge)
    if edges != sorted(edges):
        yield "IMAGES.sizes must grow: thumb <= display <= full."


def _validate_llm_vision() -> Iterable[str]:
    vision = settings.get("LLM.vision") or {}
    if parse_vision_mode(_get_value(vision, "enabled")) is None:
        yield 'LLM.vision.enabled must be "auto", true or false.'
    if _get_value(vision, "max_images") is not None:
        value = coerce_int(_get_value(vision, "max_images"))
        if value is None or not 0 <= value <= 32:
            yield "LLM.vision.max_images must be an integer between 0 and 32."
    for name, low, high in (
        ("max_edge", 64, 4096),
        ("quality", 1, 100),
        ("tokens_per_image", 1, 20000),
    ):
        value = coerce_int(_get_value(vision, name))
        if value is None or not low <= value <= high:
            yield f"LLM.vision.{name} must be an integer between {low} and {high}."


def validate_settings() -> list[str]:
    """Return a list of configuration validation error messages."""

    errors: list[str] = []
    errors.extend(_validate_llm_upstream())
    errors.extend(_validate_llm_chat_settings())
    errors.extend(_validate_llm_summary_settings())
    errors.extend(_validate_secrets())
    errors.extend(_validate_session_settings())
    errors.extend(_validate_images())
    errors.extend(_validate_llm_vision())
    return errors


__all__ = ["validate_settings"]
