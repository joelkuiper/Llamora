"""Shared helpers for demo data scripts."""

from __future__ import annotations

import logging
import textwrap
from shutil import get_terminal_size
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from rich.console import Console
from rich.rule import Rule


logger = logging.getLogger(__name__)
_console = Console(color_system=None, force_terminal=False, no_color=True)


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
    quote_chars = {
        '"',
        "'",
        "“",
        "”",
        "‘",
        "’",
        "«",
        "»",
    }
    while stripped and stripped[0] in quote_chars:
        stripped = stripped[1:].lstrip()
    while stripped and stripped[-1] in quote_chars:
        stripped = stripped[:-1].rstrip()
    return stripped


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
    logger.info("  • %s", text)


def log_block(title: str, text: str, max_chars: int = 1200) -> None:
    trimmed = text[:max_chars]
    if len(text) > max_chars:
        trimmed += "\n… (truncated)"
    log_item(title)
    for line in trimmed.splitlines():
        logger.info("     │ %s", line)


def get_console() -> Console:
    return _console


def log_rule(text: str) -> None:
    width = get_terminal_size(fallback=(120, 24)).columns
    if width < 20:
        logger.info("%s", text)
        return
    title = f" {text.strip()} " if text.strip() else ""
    if title:
        fill = max(0, width - len(title))
        left = fill // 2
        right = fill - left
        line = ("─" * left) + title + ("─" * right)
        logger.info("%s", line[:width])
    else:
        logger.info("%s", "─" * width)


def log_rich(renderable: object) -> None:
    console = get_console()
    with console.capture() as capture:
        console.print(renderable)
    for line in capture.get().splitlines():
        logger.info(line)
