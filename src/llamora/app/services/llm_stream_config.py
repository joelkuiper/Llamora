"""Configuration helpers for LLM streaming."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from llamora.settings import settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMStreamConfig:
    """Container for chat stream configuration values."""

    pending_ttl: int
    queue_limit: int
    repeat_guard_size: int | None
    repeat_guard_min_length: int | None

    @classmethod
    def from_settings(cls, settings_obj=settings) -> "LLMStreamConfig":
        """Create a configuration instance from application settings."""

        pending_ttl = cls._coerce_int_setting(
            settings_obj,
            "LLM.stream.pending_ttl",
            default=300,
            minimum=1,
        )
        queue_limit = cls._coerce_int_setting(
            settings_obj,
            "LLM.stream.queue_limit",
            default=4,
            minimum=0,
        )
        repeat_guard_size = cls._coerce_optional_int_setting(
            settings_obj,
            "LLM.stream.repeat_guard_size",
            minimum=1,
        )
        repeat_guard_min_length = cls._coerce_optional_int_setting(
            settings_obj,
            "LLM.stream.repeat_guard_min_length",
            minimum=0,
        )
        return cls(
            pending_ttl=pending_ttl,
            queue_limit=queue_limit,
            repeat_guard_size=repeat_guard_size,
            repeat_guard_min_length=repeat_guard_min_length,
        )

    @staticmethod
    def _coerce_int_setting(
        settings_obj: Any,
        key: str,
        *,
        default: int,
        minimum: int | None = None,
    ) -> int:
        raw_value = settings_obj.get(key, default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            logger.warning("Invalid %s value %r; using default %s", key, raw_value, default)
            return default
        if minimum is not None and value < minimum:
            logger.warning(
                "%s below minimum %s (got %s); clamping", key, minimum, value
            )
            return minimum
        return value

    @staticmethod
    def _coerce_optional_int_setting(
        settings_obj: Any,
        key: str,
        *,
        minimum: int | None = None,
    ) -> int | None:
        raw_value = settings_obj.get(key, None)
        if raw_value is None:
            return None
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            logger.warning("Invalid %s value %r; ignoring", key, raw_value)
            return None
        if minimum is not None and value < minimum:
            logger.warning(
                "%s below minimum %s (got %s); ignoring", key, minimum, value
            )
            return None
        return value


__all__ = ["LLMStreamConfig"]
