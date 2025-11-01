"""Llamora application package."""

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:  # pragma: no cover - import only for static analysis
    from .app import create_app as create_app
else:
    def create_app(*args: Any, **kwargs: Any):
        from .app import create_app as _create_app

        return _create_app(*args, **kwargs)


__all__ = ["create_app"]
