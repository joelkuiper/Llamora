"""Image attachments: encrypted at rest, linked to entries.

See ``doc/specs/images.md``. Each stored image has three variants, all
re-encoded without metadata; the original upload is never kept.
"""

from __future__ import annotations

from typing import Literal

Variant = Literal["thumb", "display", "full"]
VARIANTS: tuple[Variant, ...] = ("thumb", "display", "full")


class ImageRejected(ValueError):
    """An upload that cannot be stored as an image."""

    TOO_LARGE = "too_large"
    UNSUPPORTED_FORMAT = "unsupported_format"
    TOO_MANY_PIXELS = "too_many_pixels"
    CORRUPT = "corrupt"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class ImageDecryptError(ValueError):
    """An encrypted image file that is tampered with, truncated or misplaced."""
