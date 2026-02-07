"""Small helpers for numeric parsing."""

from __future__ import annotations


def parse_positive_int(value: object | None) -> int | None:
    """Return a positive integer from ``value``, or ``None`` if invalid."""

    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_positive_float(value: object | None) -> float | None:
    """Return a positive float from ``value``, or ``None`` if invalid."""

    if value is None:
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = ["parse_positive_int", "parse_positive_float"]
