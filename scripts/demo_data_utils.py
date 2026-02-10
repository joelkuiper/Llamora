"""Shared helpers for demo data scripts."""

from __future__ import annotations

import logging
import textwrap
from datetime import date, datetime, timedelta
from typing import Any, Iterable


logger = logging.getLogger(__name__)


def coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def coerce_str(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def require_value(value: Any, name: str) -> str:
    text = coerce_str(value)
    if not text:
        raise ValueError(f"Missing required config value: {name}")
    return text


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def iter_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def strip_outer_quotes(text: str) -> str:
    if not text:
        return text
    stripped = text.strip()
    pairs = [
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
    ]
    for left, right in pairs:
        if stripped.startswith(left) and stripped.endswith(right) and len(stripped) > 2:
            return stripped[1:-1].strip()
    return text


def log_wrapped(prefix: str, text: str, width: int = 100) -> None:
    if not text:
        return
    wrapped = textwrap.wrap(text, width=width) or [text]
    for idx, line in enumerate(wrapped):
        if idx == 0:
            logger.info("%s%s", prefix, line)
        else:
            logger.info("%s%s", " " * len(prefix), line)


def log_header(text: str) -> None:
    logger.info("==> %s", text)


def log_item(text: str) -> None:
    logger.info("  -> %s", text)


def log_block(title: str, text: str, max_chars: int = 1200) -> None:
    trimmed = text[:max_chars]
    if len(text) > max_chars:
        trimmed += "\n… (truncated)"
    log_item(title)
    for line in trimmed.splitlines():
        logger.info("     | %s", line)
