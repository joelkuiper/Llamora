"""Small helpers for numeric parsing."""

from __future__ import annotations


def coerce_int(
    value: object | None,
    *,
    default: int | None = None,
    minimum: int | None = None,
) -> int | None:
    """Return an int parsed from ``value`` or ``default`` when invalid."""

    if value is None:
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return default
    return parsed


def coerce_float(
    value: object | None,
    *,
    default: float | None = None,
    minimum: float | None = None,
) -> float | None:
    """Return a float parsed from ``value`` or ``default`` when invalid."""

    if value is None:
        return default
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return default
    return parsed


def parse_positive_int(value: object | None) -> int | None:
    """Return a positive integer from ``value``, or ``None`` if invalid."""

    return coerce_int(value, minimum=1)


def parse_positive_float(value: object | None) -> float | None:
    """Return a positive float from ``value``, or ``None`` if invalid."""

    parsed = coerce_float(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


__all__ = [
    "coerce_int",
    "coerce_float",
    "parse_positive_int",
    "parse_positive_float",
]
