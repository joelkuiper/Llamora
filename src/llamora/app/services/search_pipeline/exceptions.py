from __future__ import annotations

"""Exceptions raised by search pipeline components."""


class InvalidSearchQuery(ValueError):
    """Exception raised when a provided search query is invalid."""


__all__ = ["InvalidSearchQuery"]
