"""Helpers for working with frecency decay values."""

from __future__ import annotations

from typing import Any

FRECENCY_LAMBDAS: dict[str, float] = {
    "hour": 2.7701e-4,
    "day": 1.1574e-5,
    "week": 1.6534e-6,
    "month": 5.5181e-7,
}

DEFAULT_FRECENCY_DECAY: float = FRECENCY_LAMBDAS["week"]


def resolve_frecency_lambda(value: Any, *, default: float = DEFAULT_FRECENCY_DECAY) -> float:
    """Resolve a frecency decay constant.

    Accepts a float, an int, or a string key referencing :data:`FRECENCY_LAMBDAS`.
    Any other value (including non-positive numbers) results in the provided
    ``default`` being returned.
    """

    if value is None:
        return default

    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if numeric > 0 else default

    key = str(value).strip().lower()
    if not key:
        return default

    if key in FRECENCY_LAMBDAS:
        return FRECENCY_LAMBDAS[key]

    try:
        numeric = float(key)
    except (TypeError, ValueError):
        return default

    return numeric if numeric > 0 else default


__all__ = [
    "DEFAULT_FRECENCY_DECAY",
    "FRECENCY_LAMBDAS",
    "resolve_frecency_lambda",
]
